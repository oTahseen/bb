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
    InlineKeyboardButton
)

from aiogram.filters import Command

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# =====================================
# LOAD ENV
# =====================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# =====================================
# BOT
# =====================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()

# =====================================
# DATABASE
# =====================================

mongo = AsyncIOMotorClient(MONGO_URI)

db = mongo["koyeb_panel"]

users = db["users"]

# =====================================
# STATES
# =====================================

class AddAccount(StatesGroup):
    waiting_token = State()
    waiting_name = State()

# =====================================
# HELPERS
# =====================================

async def get_user(user_id: int):

    return await users.find_one({
        "telegram_id": user_id
    })

async def get_active_account(user_id: int):

    user = await get_user(user_id)

    if not user:
        return None

    active = user.get("active_account")

    for acc in user.get("accounts", []):

        if acc["id"] == active:
            return acc

    return None

async def koyeb_request(
    token,
    method,
    endpoint,
    payload=None
):

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    url = f"https://app.koyeb.com/v1{endpoint}"

    async with aiohttp.ClientSession() as session:

        async with session.request(
            method,
            url,
            headers=headers,
            json=payload
        ) as response:

            try:
                data = await response.json()
            except:
                data = await response.text()

            return response.status, data

# =====================================
# KEYBOARDS
# =====================================

start_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Add Account",
                callback_data="add_account"
            )
        ],
        [
            InlineKeyboardButton(
                text="📦 Manage Accounts",
                callback_data="manage_accounts"
            )
        ]
    ]
)

# =====================================
# START
# =====================================

@dp.message(Command("start"))
async def start(message: Message):

    text = """
<b>Koyeb Control Panel</b>

Manage your Koyeb accounts directly from Telegram.
"""

    await message.answer(
        text,
        reply_markup=start_keyboard
    )

# =====================================
# ADD ACCOUNT BUTTON
# =====================================

@dp.callback_query(F.data == "add_account")
async def add_account_button(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.set_state(
        AddAccount.waiting_token
    )

    await callback.message.edit_text(
        "Send your Koyeb API token"
    )

@dp.message(AddAccount.waiting_token)
async def save_token(
    message: Message,
    state: FSMContext
):

    token = message.text.strip()

    status, data = await koyeb_request(
        token,
        "GET",
        "/apps"
    )

    if status != 200:

        return await message.answer(
            "❌ Invalid token"
        )

    await state.update_data(
        token=token
    )

    await state.set_state(
        AddAccount.waiting_name
    )

    await message.answer(
        "Now send account name"
    )

@dp.message(AddAccount.waiting_name)
async def save_name(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    token = data["token"]

    name = message.text.strip()

    account = {
        "id": str(uuid.uuid4()),
        "name": name,
        "token": token
    }

    user = await get_user(
        message.from_user.id
    )

    if not user:

        await users.insert_one({
            "telegram_id": message.from_user.id,
            "accounts": [account],
            "active_account": account["id"]
        })

    else:

        await users.update_one(
            {
                "telegram_id": message.from_user.id
            },
            {
                "$push": {
                    "accounts": account
                }
            }
        )

    await state.clear()

    await message.answer(
        f"✅ Account <b>{name}</b> added",
        reply_markup=start_keyboard
    )

# =====================================
# MANAGE ACCOUNTS
# =====================================

@dp.callback_query(F.data == "manage_accounts")
async def manage_accounts(
    callback: CallbackQuery
):

    user = await get_user(
        callback.from_user.id
    )

    if not user:

        return await callback.message.edit_text(
            "No accounts found"
        )

    keyboard = []

    for acc in user["accounts"]:

        keyboard.append([
            InlineKeyboardButton(
                text=f"📁 {acc['name']}",
                callback_data=f"account:{acc['id']}"
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

# =====================================
# ACCOUNT PANEL
# =====================================

@dp.callback_query(F.data.startswith("account:"))
async def account_panel(callback: CallbackQuery):

    account_id = callback.data.split(":")[1]

    user = await get_user(
        callback.from_user.id
    )

    account = None

    for acc in user["accounts"]:

        if acc["id"] == account_id:
            account = acc
            break

    if not account:
        return

    await users.update_one(
        {
            "telegram_id": callback.from_user.id
        },
        {
            "$set": {
                "active_account": account_id
            }
        }
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
                    text="🚀 Deployments",
                    callback_data="deployments"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Delete Account",
                    callback_data=f"deleteacc:{account_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Back",
                    callback_data="manage_accounts"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        f"<b>{account['name']}</b>",
        reply_markup=keyboard
    )

# =====================================
# APPS
# =====================================

@dp.callback_query(F.data == "apps")
async def apps(callback: CallbackQuery):

    account = await get_active_account(
        callback.from_user.id
    )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/apps"
    )

    apps = data.get("apps", [])

    if not apps:

        return await callback.message.edit_text(
            "No apps found"
        )

    keyboard = []

    for app in apps:

        keyboard.append([
            InlineKeyboardButton(
                text=f"📦 {app['name']}",
                callback_data=f"app:{app['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="⬅ Back",
            callback_data="manage_accounts"
        )
    ])

    await callback.message.edit_text(
        "<b>Apps</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

# =====================================
# SERVICES
# =====================================

@dp.callback_query(F.data == "services")
async def services(callback: CallbackQuery):

    account = await get_active_account(
        callback.from_user.id
    )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/services"
    )

    services = data.get("services", [])

    if not services:

        return await callback.message.edit_text(
            "No services found"
        )

    keyboard = []

    for service in services:

        keyboard.append([
            InlineKeyboardButton(
                text=f"⚙ {service['name']}",
                callback_data=f"service:{service['id']}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="⬅ Back",
            callback_data="manage_accounts"
        )
    ])

    await callback.message.edit_text(
        "<b>Services</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

# =====================================
# SERVICE PANEL
# =====================================

@dp.callback_query(F.data.startswith("service:"))
async def service_panel(callback: CallbackQuery):

    service_id = callback.data.split(":")[1]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Restart",
                    callback_data=f"restart:{service_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚀 Redeploy",
                    callback_data=f"redeploy:{service_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📜 Logs",
                    callback_data=f"logs:{service_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧪 Variables",
                    callback_data=f"vars:{service_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Delete",
                    callback_data=f"delete_service:{service_id}"
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

    await callback.message.edit_text(
        f"<b>Service:</b>\n<code>{service_id}</code>",
        reply_markup=keyboard
    )

# =====================================
# RESTART SERVICE
# =====================================

@dp.callback_query(F.data.startswith("restart:"))
async def restart_service(callback: CallbackQuery):

    service_id = callback.data.split(":")[1]

    account = await get_active_account(
        callback.from_user.id
    )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "POST",
        f"/services/{service_id}/restart"
    )

    if status not in [200, 202]:

        return await callback.answer(
            "Failed",
            show_alert=True
        )

    await callback.answer(
        "Service restarted"
    )

# =====================================
# REDEPLOY SERVICE
# =====================================

@dp.callback_query(F.data.startswith("redeploy:"))
async def redeploy_service(callback: CallbackQuery):

    service_id = callback.data.split(":")[1]

    account = await get_active_account(
        callback.from_user.id
    )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "POST",
        f"/services/{service_id}/redeploy"
    )

    if status not in [200, 202]:

        return await callback.answer(
            "Failed",
            show_alert=True
        )

    await callback.answer(
        "Redeploy triggered"
    )

# =====================================
# LOGS
# =====================================

@dp.callback_query(F.data.startswith("logs:"))
async def logs(callback: CallbackQuery):

    service_id = callback.data.split(":")[1]

    await callback.message.answer(
        f"Logs API can be added here for:\n<code>{service_id}</code>"
    )

# =====================================
# DELETE SERVICE
# =====================================

@dp.callback_query(F.data.startswith("delete_service:"))
async def delete_service(callback: CallbackQuery):

    service_id = callback.data.split(":")[1]

    account = await get_active_account(
        callback.from_user.id
    )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "DELETE",
        f"/services/{service_id}"
    )

    if status not in [200, 202, 204]:

        return await callback.answer(
            "Delete failed",
            show_alert=True
        )

    await callback.message.edit_text(
        "✅ Service deleted"
    )

# =====================================
# DELETE ACCOUNT
# =====================================

@dp.callback_query(F.data.startswith("deleteacc:"))
async def delete_account(callback: CallbackQuery):

    account_id = callback.data.split(":")[1]

    await users.update_one(
        {
            "telegram_id": callback.from_user.id
        },
        {
            "$pull": {
                "accounts": {
                    "id": account_id
                }
            }
        }
    )

    await callback.message.edit_text(
        "✅ Account deleted"
    )

# =====================================
# HOME BUTTON
# =====================================

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):

    await callback.message.edit_text(
        "<b>Koyeb Control Panel</b>",
        reply_markup=start_keyboard
    )

# =====================================
# MAIN
# =====================================

async def main():

    print("Bot started")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
