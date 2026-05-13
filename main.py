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
    FSInputFile
)

from aiogram.filters import Command

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# =========================================
# LOAD ENV
# =========================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# =========================================
# BOT
# =========================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()

# =========================================
# DATABASE
# =========================================

mongo = AsyncIOMotorClient(MONGO_URI)

db = mongo["koyeb_panel"]

users = db["users"]

# =========================================
# STATES
# =========================================

class AddAccount(StatesGroup):
    waiting_token = State()

# =========================================
# API REQUEST
# =========================================

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

# =========================================
# HELPERS
# =========================================

async def get_user(user_id):

    return await users.find_one({
        "telegram_id": user_id
    })

async def get_account(
    telegram_id,
    account_id
):

    user = await get_user(telegram_id)

    if not user:
        return None

    for acc in user.get("accounts", []):

        if acc["id"] == account_id:
            return acc

    return None

# =========================================
# KEYBOARDS
# =========================================

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

# =========================================
# START
# =========================================

@dp.message(Command("start"))
async def start(message: Message):

    await message.answer(
        "<b>Koyeb Telegram Panel</b>",
        reply_markup=home_keyboard
    )

# =========================================
# HOME
# =========================================

@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):

    await callback.message.edit_text(
        "<b>Koyeb Telegram Panel</b>",
        reply_markup=home_keyboard
    )

# =========================================
# ADD ACCOUNT
# =========================================

@dp.callback_query(F.data == "add_account")
async def add_account(
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

    # =====================================
    # VALIDATE TOKEN
    # =====================================

    status, data = await koyeb_request(
        token,
        "GET",
        "/apps"
    )

    print(status)
    print(data)

    if status != 200:

        return await message.answer(
            f"❌ Invalid API token\n\n{data}"
        )

    apps = data.get("apps", [])

    if apps:

        account_name = apps[0].get(
            "name",
            "Koyeb Account"
        )

    else:
        account_name = "Koyeb Account"

    user = await get_user(
        message.from_user.id
    )

    # =====================================
    # PREVENT DUPLICATES
    # =====================================

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

    # =====================================
    # SAVE
    # =====================================

    if not user:

        await users.insert_one({
            "telegram_id": message.from_user.id,
            "accounts": [account]
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
        f"✅ Added:\n<b>{account_name}</b>",
        reply_markup=home_keyboard
    )

# =========================================
# ACCOUNTS
# =========================================

@dp.callback_query(F.data == "accounts")
async def accounts(callback: CallbackQuery):

    user = await get_user(
        callback.from_user.id
    )

    if not user:

        return await callback.message.edit_text(
            "No accounts found",
            reply_markup=home_keyboard
        )

    keyboard = []

    for i, acc in enumerate(
        user["accounts"]
    ):

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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

# =========================================
# ACCOUNT PANEL
# =========================================

@dp.callback_query(F.data.startswith("account:"))
async def account_panel(callback: CallbackQuery):

    index = int(
        callback.data.split(":")[1]
    )

    user = await get_user(
        callback.from_user.id
    )

    accounts = user.get(
        "accounts",
        []
    )

    if index >= len(accounts):
        return

    account = accounts[index]

    # SAVE ACTIVE ACCOUNT TEMP
    await users.update_one(
        {
            "telegram_id": callback.from_user.id
        },
        {
            "$set": {
                "temp_account_index": index
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

# =========================================
# APPS
# =========================================

@dp.callback_query(F.data == "apps")
async def apps(callback: CallbackQuery):

    user = await get_user(
        callback.from_user.id
    )

    account_index = user.get(
        "temp_account_index"
    )

    account = user["accounts"][
        account_index
    ]

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/apps"
    )

    print(status)
    print(data)

    apps = data.get("apps", [])

    keyboard = []

    for app in apps:

        keyboard.append([
            InlineKeyboardButton(
                text=f"📦 {app['name']}",
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

# =========================================
# SERVICES
# =========================================

@dp.callback_query(F.data == "services")
async def services(callback: CallbackQuery):

    user = await get_user(
        callback.from_user.id
    )

    account_index = user.get(
        "temp_account_index"
    )

    account = user["accounts"][
        account_index
    ]

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/services"
    )

    print(status)
    print(data)

    services = data.get(
        "services",
        []
    )

    # SAVE TEMP SERVICES
    await users.update_one(
        {
            "telegram_id": callback.from_user.id
        },
        {
            "$set": {
                "temp_services": services
            }
        }
    )

    keyboard = []

    for i, service in enumerate(
        services
    ):

        keyboard.append([
            InlineKeyboardButton(
                text=(
                    f"⚙ {service['name']} "
                    f"({service.get('status')})"
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        )
    )

# =========================================
# SERVICE PANEL
# =========================================

@dp.callback_query(F.data.startswith("service:"))
async def service_panel(callback: CallbackQuery):

    index = int(
        callback.data.split(":")[1]
    )

    user = await get_user(
        callback.from_user.id
    )

    services = user.get(
        "temp_services",
        []
    )

    if index >= len(services):
        return

    service = services[index]

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Redeploy",
                    callback_data=f"redeploy:{index}"
                )
            ],
            [
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
        f"Status: {service.get('status')}\n"
        f"Type: {service.get('type')}\n"
        f"ID:\n<code>{service['id']}</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

# =========================================
# REDEPLOY
# =========================================

@dp.callback_query(F.data.startswith("redeploy:"))
async def redeploy(callback: CallbackQuery):

    index = int(
        callback.data.split(":")[1]
    )

    user = await get_user(
        callback.from_user.id
    )

    services = user.get(
        "temp_services",
        []
    )

    account_index = user.get(
        "temp_account_index"
    )

    if index >= len(services):
        return

    service = services[index]

    service_id = service["id"]

    account = user["accounts"][
        account_index
    ]

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "POST",
        f"/services/{service_id}/redeploy"
    )

    print(status)
    print(data)

    if status not in [200, 202]:

        return await callback.answer(
            f"Failed ({status})",
            show_alert=True
        )

    await callback.answer(
        "🚀 Redeploy started"
    )

# =========================================
# LOGS
# =========================================

@dp.callback_query(F.data.startswith("logs:"))
async def logs(callback: CallbackQuery):

    index = int(
        callback.data.split(":")[1]
    )

    user = await get_user(
        callback.from_user.id
    )

    services = user.get(
        "temp_services",
        []
    )

    account_index = user.get(
        "temp_account_index"
    )

    if index >= len(services):
        return

    service = services[index]

    service_id = service["id"]

    account = user["accounts"][
        account_index
    ]

    token = account["token"]

    # =====================================
    # GET DEPLOYMENTS
    # =====================================

    status, data = await koyeb_request(
        token,
        "GET",
        "/deployments"
    )

    print(status)
    print(data)

    deployments = data.get(
        "deployments",
        []
    )

    target = None

    for dep in deployments:

        if dep.get(
            "service_id"
        ) == service_id:

            target = dep
            break

    if not target:

        return await callback.answer(
            "No deployment found",
            show_alert=True
        )

    deployment_id = target["id"]

    # =====================================
    # GET LOGS
    # =====================================

    log_status, log_data = await koyeb_request(
        token,
        "GET",
        f"/deployments/{deployment_id}/logs"
    )

    print(log_status)
    print(log_data)

    filename = (
        f"{service['name']}.txt"
    )

    with open(filename, "w") as f:

        f.write(str(log_data))

    await callback.message.answer_document(
        FSInputFile(filename)
    )

# =========================================
# DELETE SERVICE
# =========================================

@dp.callback_query(F.data.startswith("delete:"))
async def delete_service(callback: CallbackQuery):

    index = int(
        callback.data.split(":")[1]
    )

    user = await get_user(
        callback.from_user.id
    )

    services = user.get(
        "temp_services",
        []
    )

    account_index = user.get(
        "temp_account_index"
    )

    if index >= len(services):
        return

    service = services[index]

    service_id = service["id"]

    account = user["accounts"][
        account_index
    ]

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "DELETE",
        f"/services/{service_id}"
    )

    print(status)
    print(data)

    if status not in [200, 202, 204]:

        return await callback.answer(
            "Delete failed",
            show_alert=True
        )

    await callback.message.edit_text(
        "✅ Service deleted"
    )

# =========================================
# DELETE ACCOUNT
# =========================================

@dp.callback_query(
    F.data.startswith(
        "delete_account:"
    )
)
async def delete_account(
    callback: CallbackQuery
):

    index = int(
        callback.data.split(":")[1]
    )

    user = await get_user(
        callback.from_user.id
    )

    accounts = user.get(
        "accounts",
        []
    )

    if index >= len(accounts):
        return

    account = accounts[index]

    await users.update_one(
        {
            "telegram_id": callback.from_user.id
        },
        {
            "$pull": {
                "accounts": {
                    "id": account["id"]
                }
            }
        }
    )

    await callback.message.edit_text(
        "✅ Account deleted",
        reply_markup=home_keyboard
    )

# =========================================
# MAIN
# =========================================

async def main():

    print("Bot started")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
