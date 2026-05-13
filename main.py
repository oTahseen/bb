import os
import uuid
import aiohttp
import asyncio

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

mongo = AsyncIOMotorClient(MONGO_URI)
db    = mongo["koyeb_panel"]
users = db["users"]


# ─────────────────────────────────────────────────────────────────────────────
# FSM
# ─────────────────────────────────────────────────────────────────────────────

class AddAccount(StatesGroup):
    waiting_token = State()   # step 1: user sends the API key
    waiting_name  = State()   # step 2: user sends a friendly name


# ─────────────────────────────────────────────────────────────────────────────
# Koyeb API helper
#
# Base URL : https://app.koyeb.com
# Auth     : Bearer <api_key>   (dashboard-created API key)
# All endpoints are /v1/...
#
# koyeb_request(token, method, endpoint, payload=None, params=None)
#   payload  -> sent as JSON body  (POST/PUT/DELETE with body)
#   params   -> sent as URL query string  (GET filters)
# ─────────────────────────────────────────────────────────────────────────────

async def koyeb_request(token, method, endpoint, payload=None, params=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    url = f"https://app.koyeb.com{endpoint}"

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method, url, headers=headers, json=payload, params=params
        ) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = await resp.text()
            return resp.status, data


# ─────────────────────────────────────────────────────────────────────────────
# DB helper
# ─────────────────────────────────────────────────────────────────────────────

async def get_user(user_id):
    return await users.find_one({"telegram_id": user_id})


# ─────────────────────────────────────────────────────────────────────────────
# Static keyboards
# ─────────────────────────────────────────────────────────────────────────────

home_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Account", callback_data="add_account")],
        [InlineKeyboardButton(text="📦 Accounts",   callback_data="accounts")],
    ]
)


# ─────────────────────────────────────────────────────────────────────────────
# /start  &  home
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("<b>Koyeb Telegram Panel</b>", reply_markup=home_keyboard)


@dp.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Koyeb Telegram Panel</b>", reply_markup=home_keyboard
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add account – step 1: ask for token
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "add_account")
async def cb_add_account(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddAccount.waiting_token)
    await callback.message.edit_text(
        "Send your Koyeb API token.\n\n"
        "<i>Create one at: Koyeb dashboard → Account Settings → API</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add account – step 2: validate token then ask for name
#
# WHY GET /v1/apps for validation:
#   Dashboard API keys are NOT session tokens.
#   Endpoints like /v1/account/profile require a session token (from
#   email/password login) and return 404 "No user defined in session"
#   for API keys. GET /v1/apps works correctly with API keys:
#     401 = bad token
#     200 = valid (even if account has 0 apps)
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(AddAccount.waiting_token)
async def msg_receive_token(message: Message, state: FSMContext):
    token = message.text.strip()

    await message.answer("⏳ Verifying token...")

    status, data = await koyeb_request(token, "GET", "/v1/apps")

    if status == 401:
        return await message.answer(
            "❌ <b>Invalid API token</b> — Koyeb rejected it (401).\n\n"
            "Make sure you copied the full token and try again."
        )

    if status not in (200, 404):
        return await message.answer(
            f"❌ Unexpected response from Koyeb (HTTP {status}).\n"
            f"<code>{data}</code>"
        )

    # Duplicate check
    user = await get_user(message.from_user.id)
    if user:
        for acc in user.get("accounts", []):
            if acc["token"] == token:
                await state.clear()
                return await message.answer(
                    "⚠️ This token is already saved.", reply_markup=home_keyboard
                )

    await state.update_data(token=token)
    await state.set_state(AddAccount.waiting_name)

    await message.answer(
        "✅ Token is valid!\n\n"
        "Now send a <b>name</b> for this account.\n"
        "<i>Example: My Koyeb, Production, Side Project…</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add account – step 3: save name
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(AddAccount.waiting_name)
async def msg_receive_name(message: Message, state: FSMContext):
    account_name = message.text.strip()

    if not account_name:
        return await message.answer("Please send a non-empty name.")

    fsm_data = await state.get_data()
    token    = fsm_data["token"]

    account = {
        "id":    str(uuid.uuid4()),
        "token": token,
        "name":  account_name,
    }

    user = await get_user(message.from_user.id)

    if not user:
        await users.insert_one({
            "telegram_id": message.from_user.id,
            "accounts":    [account],
        })
    else:
        await users.update_one(
            {"telegram_id": message.from_user.id},
            {"$push": {"accounts": account}},
        )

    await state.clear()
    await message.answer(
        f"✅ Account <b>{account_name}</b> added!",
        reply_markup=home_keyboard,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Accounts list
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "accounts")
async def cb_accounts(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)

    if not user or not user.get("accounts"):
        return await callback.message.edit_text(
            "No accounts found.", reply_markup=home_keyboard
        )

    keyboard = [
        [InlineKeyboardButton(text=f"📁 {acc['name']}", callback_data=f"account:{i}")]
        for i, acc in enumerate(user["accounts"])
    ]
    keyboard.append([InlineKeyboardButton(text="⬅ Back", callback_data="home")])

    await callback.message.edit_text(
        "<b>Your Accounts</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Account panel
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("account:"))
async def cb_account_panel(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])
    user  = await get_user(callback.from_user.id)
    accs  = user.get("accounts", [])

    if index >= len(accs):
        return

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$set": {"temp_account_index": index}},
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Apps",          callback_data="apps")],
            [InlineKeyboardButton(text="⚙ Services",       callback_data="services")],
            [InlineKeyboardButton(text="🗑 Delete Account", callback_data=f"delete_account:{index}")],
            [InlineKeyboardButton(text="⬅ Back",           callback_data="accounts")],
        ]
    )

    await callback.message.edit_text(
        f"<b>{accs[index]['name']}</b>", reply_markup=keyboard
    )


# ─────────────────────────────────────────────────────────────────────────────
# Apps  –  GET /v1/apps
#
# API spec: ListAppsReply → { "apps": [ AppListItem, … ] }
# AppListItem fields: id, name, status (App.Status enum), domains, …
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "apps")
async def cb_apps(callback: CallbackQuery):
    user          = await get_user(callback.from_user.id)
    account_index = user.get("temp_account_index")
    token         = user["accounts"][account_index]["token"]

    status, data = await koyeb_request(token, "GET", "/v1/apps")

    if status != 200:
        return await callback.message.edit_text(
            f"❌ Failed to fetch apps (HTTP {status})\n<code>{data}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅ Back", callback_data=f"account:{account_index}")
            ]])
        )

    # Response key is "apps" → array of AppListItem
    apps_list = data.get("apps", [])

    keyboard = [
        [InlineKeyboardButton(
            text=f"📦 {app['name']} — {app.get('status', '?')}",
            callback_data="none",
        )]
        for app in apps_list
    ]
    keyboard.append([
        InlineKeyboardButton(text="⬅ Back", callback_data=f"account:{account_index}")
    ])

    text = f"<b>Apps</b> ({len(apps_list)} total)" if apps_list else "<b>Apps</b>\n\nNo apps found."
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Services  –  GET /v1/services
#
# API spec: ListServicesReply → { "services": [ ServiceListItem, … ] }
# ServiceListItem fields: id, name, type, status (Service.Status enum),
#   active_deployment_id, latest_deployment_id, …
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "services")
async def cb_services(callback: CallbackQuery):
    user          = await get_user(callback.from_user.id)
    account_index = user.get("temp_account_index")
    token         = user["accounts"][account_index]["token"]

    status, data = await koyeb_request(token, "GET", "/v1/services")

    if status != 200:
        return await callback.message.edit_text(
            f"❌ Failed to fetch services (HTTP {status})\n<code>{data}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅ Back", callback_data=f"account:{account_index}")
            ]])
        )

    # Response key is "services" → array of ServiceListItem
    services_list = data.get("services", [])

    # Persist list in DB so action handlers can look up by index
    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$set": {"temp_services": services_list}},
    )

    keyboard = [
        [InlineKeyboardButton(
            text=f"⚙ {svc['name']} — {svc.get('status', '?')}",
            callback_data=f"service:{i}",
        )]
        for i, svc in enumerate(services_list)
    ]
    keyboard.append([
        InlineKeyboardButton(text="⬅ Back", callback_data=f"account:{account_index}")
    ])

    text = f"<b>Services</b> ({len(services_list)} total)" if services_list else "<b>Services</b>\n\nNo services found."
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Service panel
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("service:"))
async def cb_service_panel(callback: CallbackQuery):
    index         = int(callback.data.split(":")[1])
    user          = await get_user(callback.from_user.id)
    services_list = user.get("temp_services", [])

    if index >= len(services_list):
        return

    svc = services_list[index]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Redeploy", callback_data=f"redeploy:{index}"),
                InlineKeyboardButton(text="⏸ Pause",    callback_data=f"pause:{index}"),
            ],
            [
                InlineKeyboardButton(text="▶ Resume",   callback_data=f"resume:{index}"),
                InlineKeyboardButton(text="📜 Logs",    callback_data=f"logs:{index}"),
            ],
            [InlineKeyboardButton(text="🗑 Delete",     callback_data=f"delete_service:{index}")],
            [InlineKeyboardButton(text="⬅ Back",        callback_data="services")],
        ]
    )

    # ServiceListItem fields: id, name, type, status, latest_deployment_id
    await callback.message.edit_text(
        f"<b>{svc['name']}</b>\n\n"
        f"Status: <code>{svc.get('status', '?')}</code>\n"
        f"Type:   <code>{svc.get('type', '?')}</code>\n"
        f"ID:     <code>{svc['id']}</code>",
        reply_markup=keyboard,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper – fetch service object + token from DB
# ─────────────────────────────────────────────────────────────────────────────

async def get_service_and_token(callback, index):
    user          = await get_user(callback.from_user.id)
    services_list = user.get("temp_services", [])
    account_index = user.get("temp_account_index")
    token         = user["accounts"][account_index]["token"]
    return services_list[index], token


# ─────────────────────────────────────────────────────────────────────────────
# Redeploy  –  POST /v1/services/{id}/redeploy
#
# API spec:
#   path param : id  (service ID)
#   body param : info  →  RedeployRequest.Info
#     { deployment_group, sha, use_cache, skip_build }  — all optional
#   response   : RedeployReply → { deployment: Deployment }
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("redeploy:"))
async def cb_redeploy(callback: CallbackQuery):
    index      = int(callback.data.split(":")[1])
    svc, token = await get_service_and_token(callback, index)

    # Body is required by schema; all fields optional so {} = redeploy with defaults
    status, data = await koyeb_request(
        token,
        "POST",
        f"/v1/services/{svc['id']}/redeploy",
        payload={},   # RedeployRequest.Info — all fields optional
    )

    if status not in (200, 201, 202):
        return await callback.answer(
            f"❌ Redeploy failed (HTTP {status}): {data.get('message', '') if isinstance(data, dict) else data}",
            show_alert=True,
        )

    await callback.answer("🚀 Redeploy triggered!", show_alert=False)


# ─────────────────────────────────────────────────────────────────────────────
# Pause  –  POST /v1/services/{id}/pause
#
# API spec:
#   path param : id  (service ID)
#   no body
#   response   : PauseServiceReply  (empty object)
#   allowed statuses: STARTING, HEALTHY, DEGRADED, UNHEALTHY, RESUMING
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("pause:"))
async def cb_pause(callback: CallbackQuery):
    index      = int(callback.data.split(":")[1])
    svc, token = await get_service_and_token(callback, index)

    status, data = await koyeb_request(
        token,
        "POST",
        f"/v1/services/{svc['id']}/pause",
    )

    if status not in (200, 201, 202):
        return await callback.answer(
            f"❌ Pause failed (HTTP {status}): {data.get('message', '') if isinstance(data, dict) else data}",
            show_alert=True,
        )

    await callback.answer("⏸ Service paused", show_alert=False)


# ─────────────────────────────────────────────────────────────────────────────
# Resume  –  POST /v1/services/{id}/resume
#
# API spec:
#   path param  : id  (service ID)
#   query params: skip_build (bool, optional), use_cache (bool, optional)
#   no body
#   response    : ResumeServiceReply  (empty object)
#   allowed statuses: PAUSED
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("resume:"))
async def cb_resume(callback: CallbackQuery):
    index      = int(callback.data.split(":")[1])
    svc, token = await get_service_and_token(callback, index)

    # skip_build=false → do a full rebuild on resume (safest default)
    status, data = await koyeb_request(
        token,
        "POST",
        f"/v1/services/{svc['id']}/resume",
        params={"skip_build": "false"},
    )

    if status not in (200, 201, 202):
        return await callback.answer(
            f"❌ Resume failed (HTTP {status}): {data.get('message', '') if isinstance(data, dict) else data}",
            show_alert=True,
        )

    await callback.answer("▶ Service resumed", show_alert=False)


# ─────────────────────────────────────────────────────────────────────────────
# Logs  –  GET /v1/streams/logs/query
#
# API spec:
#   method : GET  (NOT POST — query params only, no body)
#   required: at least one of app_id, service_id, deployment_id,
#             regional_deployment_id, instance_ids
#   response: QueryLogsReply → { "data": [ LogEntry, … ], "pagination": … }
#             LogEntry fields: msg, created_at, labels (object)
#
# BUG IN PREVIOUS CODE: used "logs" as response key → always empty.
# CORRECT key is "data".
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("logs:"))
async def cb_logs(callback: CallbackQuery):
    index      = int(callback.data.split(":")[1])
    svc, token = await get_service_and_token(callback, index)

    params = {
        "service_id": svc["id"],
        "type":       "runtime",   # "runtime" or "build"
        "order":      "desc",
        "limit":      "100",
    }

    status, data = await koyeb_request(
        token, "GET", "/v1/streams/logs/query", params=params
    )

    if status != 200:
        return await callback.answer(
            f"❌ Failed to fetch logs (HTTP {status}): {data.get('message', '') if isinstance(data, dict) else data}",
            show_alert=True,
        )

    # Correct response key is "data", not "logs"
    log_entries = data.get("data", []) if isinstance(data, dict) else []

    if not log_entries:
        return await callback.answer("No logs found for this service.", show_alert=True)

    lines = []
    for entry in log_entries:
        ts     = entry.get("created_at", "")
        labels = entry.get("labels", {})
        stream = labels.get("stream", "") if isinstance(labels, dict) else ""
        msg    = entry.get("msg", "")
        lines.append(f"[{ts}] [{stream}] {msg}" if ts else msg)

    # log_entries are returned desc (newest first), reverse for chronological order
    lines.reverse()

    filename = f"{svc['name']}_logs.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    await callback.message.answer_document(
        FSInputFile(filename),
        caption=f"📜 Logs for <b>{svc['name']}</b>",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Delete service  –  DELETE /v1/services/{id}
#
# API spec:
#   path param : id
#   response   : DeleteServiceReply  (empty object, HTTP 200)
#   allowed for all statuses
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("delete_service:"))
async def cb_delete_service(callback: CallbackQuery):
    index      = int(callback.data.split(":")[1])
    svc, token = await get_service_and_token(callback, index)

    status, data = await koyeb_request(
        token, "DELETE", f"/v1/services/{svc['id']}"
    )

    if status not in (200, 202, 204):
        return await callback.answer(
            f"❌ Delete failed (HTTP {status}): {data.get('message', '') if isinstance(data, dict) else data}",
            show_alert=True,
        )

    await callback.message.edit_text(
        f"✅ Service <b>{svc['name']}</b> deleted.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅ Back to Services", callback_data="services")
        ]])
    )


# ─────────────────────────────────────────────────────────────────────────────
# Delete account  –  removes from MongoDB only (no Koyeb API call needed)
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("delete_account:"))
async def cb_delete_account(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])
    user  = await get_user(callback.from_user.id)
    accs  = user.get("accounts", [])

    if index >= len(accs):
        return

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$pull": {"accounts": {"id": accs[index]["id"]}}},
    )

    await callback.message.edit_text("✅ Account deleted.", reply_markup=home_keyboard)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
