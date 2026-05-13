import os
import uuid
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta

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
    FSInputFile
)

from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()

mongo = AsyncIOMotorClient(MONGO_URI)

db = mongo["koyeb_panel"]

users = db["users"]


class AddAccount(StatesGroup):
    waiting_token = State()


# ---------------------------------------------------------------------------
# Koyeb API helper
# ---------------------------------------------------------------------------

async def koyeb_request(
    token,
    method,
    endpoint,
    payload=None,
    params=None
):
    """
    Make an authenticated request to the Koyeb API.

    Base URL:  https://app.koyeb.com
    All paths already include /v1/ prefix.

    Args:
        token   – raw API key (without "Bearer " prefix)
        method  – HTTP verb ("GET", "POST", "DELETE", …)
        endpoint– path starting with /v1/…
        payload – dict sent as JSON body (for POST/PUT)
        params  – dict sent as query string parameters (for GET)
    """
    if not token.startswith("Bearer "):
        auth_header = f"Bearer {token}"
    else:
        auth_header = token

    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json"
    }

    url = f"https://app.koyeb.com{endpoint}"

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method,
            url,
            headers=headers,
            json=payload,
            params=params
        ) as response:
            try:
                data = await response.json()
            except Exception:
                data = await response.text()

            return response.status, data


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_user(user_id):
    return await users.find_one({"telegram_id": user_id})


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

home_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Add Account",
                callback_data="add_account"
            )
        ],
        [
            InlineKeyboardButton(
                text="📦 Accounts",
                callback_data="accounts"
            )
        ]
    ]
)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "<b>Koyeb Telegram Panel</b>",
        reply_markup=home_keyboard
    )


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Koyeb Telegram Panel</b>",
        reply_markup=home_keyboard
    )


# ---------------------------------------------------------------------------
# Add account
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "add_account")
async def add_account(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.set_state(AddAccount.waiting_token)
    await callback.message.edit_text(
        "Send your Koyeb API token"
    )


@dp.message(AddAccount.waiting_token)
async def save_token(
    message: Message,
    state: FSMContext
):
    token = message.text.strip()

    # ------------------------------------------------------------------
    # FIX: validate token via GET /v1/account/profile
    #      (returns the current user; fails with 401 on bad token)
    #      Previous code used /account/organizations which does not
    #      authenticate the same way and caused the 401 error.
    # ------------------------------------------------------------------
    status, data = await koyeb_request(
        token,
        "GET",
        "/v1/account/profile"
    )

    if status != 200:
        return await message.answer(
            f"❌ Invalid API Token\n\n{data}"
        )

    # Extract a friendly account name from the user profile
    user_data = data.get("user", {})
    account_name = (
        user_data.get("name")
        or user_data.get("email")
        or "Koyeb Account"
    )

    # ------------------------------------------------------------------
    # Optionally also fetch the current organisation name
    # GET /v1/account/organization  (singular) → { "organization": {...} }
    # ------------------------------------------------------------------
    org_status, org_data = await koyeb_request(
        token,
        "GET",
        "/v1/account/organization"
    )
    if org_status == 200:
        org = org_data.get("organization", {})
        org_name = org.get("name")
        if org_name:
            account_name = org_name

    user = await get_user(message.from_user.id)

    if user:
        for acc in user.get("accounts", []):
            if acc["token"] == token:
                return await message.answer(
                    "⚠ Account already added"
                )

    account = {
        "id": str(uuid.uuid4()),
        "token": token,
        "name": account_name
    }

    if not user:
        await users.insert_one({
            "telegram_id": message.from_user.id,
            "accounts": [account]
        })
    else:
        await users.update_one(
            {"telegram_id": message.from_user.id},
            {"$push": {"accounts": account}}
        )

    await state.clear()

    await message.answer(
        f"✅ Added\n\n<b>{account_name}</b>",
        reply_markup=home_keyboard
    )


# ---------------------------------------------------------------------------
# Accounts list
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "accounts")
async def accounts(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)

    if not user:
        return await callback.message.edit_text(
            "No accounts found",
            reply_markup=home_keyboard
        )

    keyboard = []

    for i, acc in enumerate(user["accounts"]):
        keyboard.append([
            InlineKeyboardButton(
                text=f"📁 {acc['name']}",
                callback_data=f"account:{i}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="⬅ Back",
            callback_data="home"
        )
    ])

    await callback.message.edit_text(
        "<b>Your Accounts</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ---------------------------------------------------------------------------
# Account panel
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("account:"))
async def account_panel(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    user = await get_user(callback.from_user.id)

    accs = user.get("accounts", [])

    if index >= len(accs):
        return

    account = accs[index]

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$set": {"temp_account_index": index}}
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Apps",
                    callback_data="apps"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙ Services",
                    callback_data="services"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Delete Account",
                    callback_data=f"delete_account:{index}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="accounts"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"<b>{account['name']}</b>",
        reply_markup=keyboard
    )


# ---------------------------------------------------------------------------
# Apps  –  GET /v1/apps
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "apps")
async def apps(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    account_index = user.get("temp_account_index")
    account = user["accounts"][account_index]
    token = account["token"]

    # FIX: endpoint is /v1/apps  (was missing /v1 prefix in original)
    status, data = await koyeb_request(token, "GET", "/v1/apps")

    apps_list = data.get("apps", [])

    keyboard = []

    for app in apps_list:
        keyboard.append([
            InlineKeyboardButton(
                text=(
                    f"📦 {app['name']} "
                    f"({app.get('status', 'unknown')})"
                ),
                callback_data="none"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="⬅ Back",
            callback_data=f"account:{account_index}"
        )
    ])

    await callback.message.edit_text(
        "<b>Apps</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ---------------------------------------------------------------------------
# Services  –  GET /v1/services
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "services")
async def services(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    account_index = user.get("temp_account_index")
    account = user["accounts"][account_index]
    token = account["token"]

    # FIX: endpoint is /v1/services  (was missing /v1 prefix in original)
    status, data = await koyeb_request(token, "GET", "/v1/services")

    services_list = data.get("services", [])

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$set": {"temp_services": services_list}}
    )

    keyboard = []

    for i, service in enumerate(services_list):
        keyboard.append([
            InlineKeyboardButton(
                text=(
                    f"⚙ {service['name']} "
                    f"({service.get('status', 'unknown')})"
                ),
                callback_data=f"service:{i}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="⬅ Back",
            callback_data=f"account:{account_index}"
        )
    ])

    await callback.message.edit_text(
        "<b>Services</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


# ---------------------------------------------------------------------------
# Service panel
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("service:"))
async def service_panel(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    user = await get_user(callback.from_user.id)
    services_list = user.get("temp_services", [])

    if index >= len(services_list):
        return

    service = services_list[index]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Redeploy",
                    callback_data=f"redeploy:{index}"
                ),
                InlineKeyboardButton(
                    text="⏸ Pause",
                    callback_data=f"pause:{index}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="▶ Resume",
                    callback_data=f"resume:{index}"
                ),
                InlineKeyboardButton(
                    text="📜 Logs",
                    callback_data=f"logs:{index}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Delete",
                    callback_data=f"delete:{index}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="services"
                )
            ]
        ]
    )

    text = (
        f"<b>{service['name']}</b>\n\n"
        f"Status: {service.get('status', 'unknown')}\n"
        f"Type: {service.get('type', 'unknown')}\n"
        f"ID:\n<code>{service['id']}</code>"
    )

    await callback.message.edit_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Helper – retrieve service + token from DB
# ---------------------------------------------------------------------------

async def get_service_and_token(callback, index):
    user = await get_user(callback.from_user.id)

    services_list = user.get("temp_services", [])
    account_index = user.get("temp_account_index")

    service = services_list[index]
    account = user["accounts"][account_index]
    token = account["token"]

    return service, token


# ---------------------------------------------------------------------------
# Redeploy  –  POST /v1/services/{id}/redeploy
#
# FIX: The API requires a JSON body matching RedeployRequest.Info.
#      We send an empty object {} which uses all defaults (latest build,
#      with cache).  The endpoint path already existed but the body was
#      missing, causing 400/422 errors on some Koyeb tenants.
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("redeploy:"))
async def redeploy(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    service, token = await get_service_and_token(callback, index)

    # Body is required by the API schema (RedeployRequest.Info).
    # All fields are optional, so {} triggers a standard redeploy.
    status, data = await koyeb_request(
        token,
        "POST",
        f"/v1/services/{service['id']}/redeploy",
        payload={}
    )

    if status not in [200, 201, 202]:
        return await callback.answer(
            f"❌ Redeploy failed ({status})",
            show_alert=True
        )

    await callback.answer("🚀 Redeploy started")


# ---------------------------------------------------------------------------
# Pause  –  POST /v1/services/{id}/pause
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("pause:"))
async def pause_service(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    service, token = await get_service_and_token(callback, index)

    status, data = await koyeb_request(
        token,
        "POST",
        f"/v1/services/{service['id']}/pause"
    )

    if status not in [200, 201, 202]:
        return await callback.answer(
            "❌ Pause failed",
            show_alert=True
        )

    await callback.answer("⏸ Service paused")


# ---------------------------------------------------------------------------
# Resume  –  POST /v1/services/{id}/resume
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("resume:"))
async def resume_service(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    service, token = await get_service_and_token(callback, index)

    status, data = await koyeb_request(
        token,
        "POST",
        f"/v1/services/{service['id']}/resume"
    )

    if status not in [200, 201, 202]:
        return await callback.answer(
            "❌ Resume failed",
            show_alert=True
        )

    await callback.answer("▶ Service resumed")


# ---------------------------------------------------------------------------
# Logs  –  GET /v1/streams/logs/query  (query-string params, NOT POST body)
#
# FIX: The original code used POST with a JSON body.
#      The API spec defines this as a GET endpoint with query parameters.
#      We pass service_id as a query param and fetch the last 15 min of logs.
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("logs:"))
async def logs(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    service, token = await get_service_and_token(callback, index)

    # Build query params per the API spec
    params = {
        "service_id": service["id"],
        "type": "runtime",         # "build" or "runtime"
        "order": "desc",
        "limit": "200"
    }

    # Optional: add deployment_id if available for more precise filtering
    deployment_id = service.get("latest_deployment_id")
    if deployment_id:
        params["deployment_id"] = deployment_id

    status, data = await koyeb_request(
        token,
        "GET",
        "/v1/streams/logs/query",
        params=params
    )

    if status != 200:
        return await callback.answer(
            f"❌ Could not fetch logs ({status})",
            show_alert=True
        )

    # Format log lines
    log_entries = data.get("logs", [])

    if not log_entries:
        return await callback.answer(
            "No logs found for this service",
            show_alert=True
        )

    lines = []
    for entry in log_entries:
        msg = entry.get("msg", "")
        ts = entry.get("created_at", "")
        stream = entry.get("labels", {}).get("stream", "")
        line = f"[{ts}] [{stream}] {msg}" if ts else msg
        lines.append(line)

    filename = f"{service['name']}_logs.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(reversed(lines)))   # oldest → newest

    await callback.message.answer_document(FSInputFile(filename))


# ---------------------------------------------------------------------------
# Delete service  –  DELETE /v1/services/{id}
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("delete:"))
async def delete_service(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    service, token = await get_service_and_token(callback, index)

    status, data = await koyeb_request(
        token,
        "DELETE",
        f"/v1/services/{service['id']}"
    )

    if status not in [200, 202, 204]:
        return await callback.answer(
            "❌ Delete failed",
            show_alert=True
        )

    await callback.message.edit_text("✅ Service deleted")


# ---------------------------------------------------------------------------
# Delete account (local only – removes from MongoDB)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data.startswith("delete_account:"))
async def delete_account(callback: CallbackQuery):
    index = int(callback.data.split(":")[1])

    user = await get_user(callback.from_user.id)

    accs = user.get("accounts", [])

    if index >= len(accs):
        return

    account = accs[index]

    await users.update_one(
        {"telegram_id": callback.from_user.id},
        {"$pull": {"accounts": {"id": account["id"]}}}
    )

    await callback.message.edit_text(
        "✅ Account deleted",
        reply_markup=home_keyboard
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
