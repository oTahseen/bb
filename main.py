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
db = mongo["koyeb_panel"]
users = db["users"]


# ─────────────────────────────────────────────────────────────────────────────
# FSM
# ─────────────────────────────────────────────────────────────────────────────

class AddAccount(StatesGroup):
    waiting_token = State()   # step 1 – user sends the API key
    waiting_name  = State()   # step 2 – user sends a friendly name


# ─────────────────────────────────────────────────────────────────────────────
# Koyeb API helper
# ─────────────────────────────────────────────────────────────────────────────

async def koyeb_request(token, method, endpoint, payload=None, params=None):
    """
    All Koyeb dashboard API keys are Bearer tokens.
    Base URL: https://app.koyeb.com
    endpoint must start with /v1/...
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
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
# Shared keyboards
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
        "<i>You can create one at Koyeb dashboard → Account Settings → API.</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add account – step 2: validate token, then ask for name
#
# WHY /v1/apps and NOT /v1/account/profile or /v1/account/organization:
#   Dashboard API keys are NOT session tokens.  The /account/* profile
#   endpoints require a session token from email/password login and return
#   404 "No user defined in session" for regular API keys.
#   GET /v1/apps works correctly with any valid API key.
#   401  → bad token
#   200  → valid (even if the account has zero apps)
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(AddAccount.waiting_token)
async def msg_receive_token(message: Message, state: FSMContext):
    token = message.text.strip()

    await message.answer("⏳ Verifying token...")

    status, data = await koyeb_request(token, "GET", "/v1/apps")

    if status == 401:
        return await message.answer(
            "❌ <b>Invalid API token</b> (401 Unauthorized).\n\n"
            "Please double-check you copied the full token and try again."
        )

    if status not in (200, 404):
        # anything other than 200/404 is unexpected
        return await message.answer(
            f"❌ Unexpected response from Koyeb (HTTP {status}).\n\n"
            f"<code>{data}</code>"
        )

    # Check for duplicate token
    user = await get_user(message.from_user.id)
    if user:
        for acc in user.get("accounts", []):
            if acc["token"] == token:
                await state.clear()
                return await message.answer(
                    "⚠️ This token is already saved.", reply_markup=home_keyboard
                )

    # Token is valid – store it in FSM and move to step 2
    await state.update_data(token=token)
    await state.set_state(AddAccount.waiting_name)

    await message.answer(
        "✅ Token is valid!\n\n"
        "Now send a <b>name</b> for this account so you can identify it later.\n"
        "<i>Example: My Koyeb, Production, Side Project…</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Add account – step 3: save name → done
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(AddAccount.waiting_name)
async def msg_receive_name(message: Message, state: FSMContext):
    account_name = message.text.strip()

    if not account_name:
        return await message.answer("Please send a non-empty name.")

    fsm_data = await state.get_data()
    token = fsm_data["token"]

    account = {
        "id":    str(uuid.uuid4()),
        "token": token,
        "name":  account_name,
    }

    user = await get_user(message.from_user.id)

    if not user:
        await users.insert_one({
            "telegram_id": message.from_user.id,
            "accounts": [account],
        })
    else:
        await users.update_one(
            {"telegram_id": message.from_user.id},
            {"$push": {"accounts": account}},
        )

    await state.clear()
    await message.answer(
        f"✅ Account <b>{account_name}</b> added successfully!",
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

    account = accs[index]

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$set": {"temp_account_index": index}},
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Apps",           callback_data="apps")],
            [InlineKeyboardButton(text="⚙ Services",        callback_data="services")],
            [InlineKeyboardButton(text="🗑 Delete Account",  callback_data=f"delete_account:{index}")],
            [InlineKeyboardButton(text="⬅ Back",            callback_data="accounts")],
        ]
    )

    await callback.message.edit_text(
        f"<b>{account['name']}</b>", reply_markup=keyboard
    )


# ─────────────────────────────────────────────────────────────────────────────
# Apps  –  GET /v1/apps
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "apps")
async def cb_apps(callback: CallbackQuery):
    user          = await get_user(callback.from_user.id)
    account_index = user.get("temp_account_index")
    token         = user["accounts"][account_index]["token"]

    status, data = await koyeb_request(token, "GET", "/v1/apps")
    apps_list    = data.get("apps", []) if isinstance(data, dict) else []

    keyboard = [
        [InlineKeyboardButton(
            text=f"📦 {app['name']} ({app.get('status', '?')})",
            callback_data="none",
        )]
        for app in apps_list
    ]
    keyboard.append([
        InlineKeyboardButton(text="⬅ Back", callback_data=f"account:{account_index}")
    ])

    text = "<b>Apps</b>" if apps_list else "<b>Apps</b>\n\nNo apps found."
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Services  –  GET /v1/services
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "services")
async def cb_services(callback: CallbackQuery):
    user          = await get_user(callback.from_user.id)
    account_index = user.get("temp_account_index")
    token         = user["accounts"][account_index]["token"]

    status, data  = await koyeb_request(token, "GET", "/v1/services")
    services_list = data.get("services", []) if isinstance(data, dict) else []

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$set": {"temp_services": services_list}},
    )

    keyboard = [
        [InlineKeyboardButton(
            text=f"⚙ {svc['name']} ({svc.get('status', '?')})",
            callback_data=f"service:{i}",
        )]
        for i, svc in enumerate(services_list)
    ]
    keyboard.append([
        InlineKeyboardButton(text="⬅ Back", callback_data=f"account:{account_index}")
    ])

    text = "<b>Services</b>" if services_list else "<b>Services</b>\n\nNo services found."
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
            [InlineKeyboardButton(text="🗑 Delete",     callback_data=f"delete:{index}")],
            [InlineKeyboardButton(text="⬅ Back",        callback_data="services")],
        ]
    )

    await callback.message.edit_text(
        f"<b>{svc['name']}</b>\n\n"
        f"Status: {svc.get('status', '?')}\n"
        f"Type: {svc.get('type', '?')}\n"
        f"ID: <code>{svc['id']}</code>",
        reply_markup=keyboard,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper
# ─────────────────────────────────────────────────────────────────────────────

async def get_service_and_token(callback, index):
    user          = await get_user(callback.from_user.id)
    services_list = user.get("temp_services", [])
    account_index = user.get("temp_account_index")
    token         = user["accounts"][account_index]["token"]
    return services_list[index], token


# ─────────────────────────────────────────────────────────────────────────────
# Redeploy  –  POST /v1/services/{id}/redeploy
# Body {} is required by the schema (RedeployRequest.Info, all fields optional)
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("redeploy:"))
async def cb_redeploy(callback: CallbackQuery):
    index       = int(callback.data.split(":")[1])
    svc, token  = await get_service_and_token(callback, index)

    status, data = await koyeb_request(
        token, "POST", f"/v1/services/{svc['id']}/redeploy", payload={}
    )

    if status not in (200, 201, 202):
        return await callback.answer(f"❌ Redeploy failed ({status})", show_alert=True)

    await callback.answer("🚀 Redeploy started!")


# ─────────────────────────────────────────────────────────────────────────────
# Pause  –  POST /v1/services/{id}/pause
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("pause:"))
async def cb_pause(callback: CallbackQuery):
    index       = int(callback.data.split(":")[1])
    svc, token  = await get_service_and_token(callback, index)

    status, _ = await koyeb_request(token, "POST", f"/v1/services/{svc['id']}/pause")

    if status not in (200, 201, 202):
        return await callback.answer("❌ Pause failed", show_alert=True)

    await callback.answer("⏸ Service paused")


# ─────────────────────────────────────────────────────────────────────────────
# Resume  –  POST /v1/services/{id}/resume
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("resume:"))
async def cb_resume(callback: CallbackQuery):
    index       = int(callback.data.split(":")[1])
    svc, token  = await get_service_and_token(callback, index)

    status, _ = await koyeb_request(token, "POST", f"/v1/services/{svc['id']}/resume")

    if status not in (200, 201, 202):
        return await callback.answer("❌ Resume failed", show_alert=True)

    await callback.answer("▶ Service resumed")


# ─────────────────────────────────────────────────────────────────────────────
# Logs  –  GET /v1/streams/logs/query  (query params, NOT a POST body)
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("logs:"))
async def cb_logs(callback: CallbackQuery):
    index       = int(callback.data.split(":")[1])
    svc, token  = await get_service_and_token(callback, index)

    params = {
        "service_id": svc["id"],
        "type":       "runtime",
        "order":      "desc",
        "limit":      "200",
    }
    if svc.get("latest_deployment_id"):
        params["deployment_id"] = svc["latest_deployment_id"]

    status, data = await koyeb_request(
        token, "GET", "/v1/streams/logs/query", params=params
    )

    if status != 200:
        return await callback.answer(
            f"❌ Could not fetch logs ({status})", show_alert=True
        )

    log_entries = data.get("logs", []) if isinstance(data, dict) else []

    if not log_entries:
        return await callback.answer("No logs found.", show_alert=True)

    lines = []
    for entry in log_entries:
        ts     = entry.get("created_at", "")
        stream = entry.get("labels", {}).get("stream", "")
        msg    = entry.get("msg", "")
        lines.append(f"[{ts}] [{stream}] {msg}" if ts else msg)

    filename = f"{svc['name']}_logs.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(reversed(lines)))

    await callback.message.answer_document(FSInputFile(filename))


# ─────────────────────────────────────────────────────────────────────────────
# Delete service  –  DELETE /v1/services/{id}
# ─────────────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("delete:"))
async def cb_delete_service(callback: CallbackQuery):
    index       = int(callback.data.split(":")[1])
    svc, token  = await get_service_and_token(callback, index)

    status, _ = await koyeb_request(token, "DELETE", f"/v1/services/{svc['id']}")

    if status not in (200, 202, 204):
        return await callback.answer("❌ Delete failed", show_alert=True)

    await callback.message.edit_text("✅ Service deleted.")


# ─────────────────────────────────────────────────────────────────────────────
# Delete account (removes from MongoDB only)
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
