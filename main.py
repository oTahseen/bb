import os
import uuid
import aiohttp
import asyncio

from datetime import datetime
from dotenv import load_dotenv

from motor.motor_asyncio import AsyncIOMotorClient

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

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

db = mongo["koyeb_manager"]

users = db["users"]

# =========================================
# FSM STATES
# =========================================

class AddAccount(StatesGroup):
    waiting_token = State()
    waiting_name = State()

# =========================================
# HELPERS
# =========================================

async def get_user(user_id: int):

    return await users.find_one({
        "telegram_id": user_id
    })

async def get_active_account(user_id: int):

    user = await get_user(user_id)

    if not user:
        return None

    active_id = user.get("active_account")

    for account in user.get("accounts", []):

        if account["id"] == active_id:
            return account

    return None

async def koyeb_request(
    token: str,
    method: str,
    endpoint: str
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
            headers=headers
        ) as response:

            try:
                data = await response.json()
            except:
                data = await response.text()

            return response.status, data

# =========================================
# START
# =========================================

@dp.message(Command("start"))
async def start(message: Message):

    text = """
<b>Koyeb Multi Account Manager</b>

Account Commands:

/addaccount
/accounts
/use ACCOUNT_NUMBER
/delete ACCOUNT_NUMBER

Koyeb Commands:

/apps
/services
/deployments
/restart SERVICE_ID
/redeploy SERVICE_ID
"""

    await message.answer(text)

# =========================================
# ADD ACCOUNT
# =========================================

@dp.message(Command("addaccount"))
async def add_account(message: Message, state: FSMContext):

    await state.set_state(AddAccount.waiting_token)

    await message.answer(
        "Send your Koyeb API token"
    )

@dp.message(AddAccount.waiting_token)
async def save_token(message: Message, state: FSMContext):

    token = message.text.strip()

    status, data = await koyeb_request(
        token,
        "GET",
        "/apps"
    )

    if status != 200:

        return await message.answer(
            "❌ Invalid Koyeb API token"
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
async def save_account(message: Message, state: FSMContext):

    name = message.text.strip()

    data = await state.get_data()

    token = data["token"]

    user = await get_user(
        message.from_user.id
    )

    account = {
        "id": str(uuid.uuid4()),
        "name": name,
        "token": token,
        "created_at": datetime.utcnow().isoformat()
    }

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
        f"✅ Account <b>{name}</b> added"
    )

# =========================================
# ACCOUNTS
# =========================================

@dp.message(Command("accounts"))
async def accounts(message: Message):

    user = await get_user(
        message.from_user.id
    )

    if not user:

        return await message.answer(
            "No accounts found"
        )

    text = "<b>Your Accounts</b>\n\n"

    for i, acc in enumerate(
        user["accounts"],
        start=1
    ):

        active = ""

        if acc["id"] == user.get(
            "active_account"
        ):
            active = " ✅ ACTIVE"

        text += (
            f"{i}. {acc['name']}"
            f"{active}\n"
        )

    await message.answer(text)

# =========================================
# SWITCH ACCOUNT
# =========================================

@dp.message(Command("use"))
async def use_account(message: Message):

    args = message.text.split()

    if len(args) < 2:

        return await message.answer(
            "Usage:\n/use 1"
        )

    try:
        number = int(args[1])
    except:
        return await message.answer(
            "Invalid number"
        )

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    accounts = user["accounts"]

    if number < 1 or number > len(accounts):

        return await message.answer(
            "Invalid account number"
        )

    selected = accounts[number - 1]

    await users.update_one(
        {
            "telegram_id": message.from_user.id
        },
        {
            "$set": {
                "active_account": selected["id"]
            }
        }
    )

    await message.answer(
        f"✅ Active account:\n"
        f"<b>{selected['name']}</b>"
    )

# =========================================
# DELETE ACCOUNT
# =========================================

@dp.message(Command("delete"))
async def delete_account(message: Message):

    args = message.text.split()

    if len(args) < 2:

        return await message.answer(
            "Usage:\n/delete 1"
        )

    try:
        number = int(args[1])
    except:
        return await message.answer(
            "Invalid number"
        )

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    accounts = user["accounts"]

    if number < 1 or number > len(accounts):

        return await message.answer(
            "Invalid account number"
        )

    selected = accounts[number - 1]

    await users.update_one(
        {
            "telegram_id": message.from_user.id
        },
        {
            "$pull": {
                "accounts": {
                    "id": selected["id"]
                }
            }
        }
    )

    await message.answer(
        f"🗑 Deleted:\n"
        f"<b>{selected['name']}</b>"
    )

# =========================================
# APPS
# =========================================

@dp.message(Command("apps"))
async def apps(message: Message):

    account = await get_active_account(
        message.from_user.id
    )

    if not account:

        return await message.answer(
            "No active account"
        )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/apps"
    )

    if status != 200:

        return await message.answer(
            "API error"
        )

    apps = data.get("apps", [])

    if not apps:

        return await message.answer(
            "No apps found"
        )

    text = "<b>Apps</b>\n\n"

    for app in apps:

        text += (
            f"<b>{app['name']}</b>\n"
            f"ID: <code>{app['id']}</code>\n"
            f"Status: {app.get('status')}\n\n"
        )

    await message.answer(text)

# =========================================
# SERVICES
# =========================================

@dp.message(Command("services"))
async def services(message: Message):

    account = await get_active_account(
        message.from_user.id
    )

    if not account:

        return await message.answer(
            "No active account"
        )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/services"
    )

    if status != 200:

        return await message.answer(
            "API error"
        )

    services = data.get("services", [])

    if not services:

        return await message.answer(
            "No services found"
        )

    text = "<b>Services</b>\n\n"

    for service in services:

        text += (
            f"<b>{service['name']}</b>\n"
            f"ID: <code>{service['id']}</code>\n"
            f"Type: {service.get('type')}\n"
            f"Status: {service.get('status')}\n\n"
        )

    await message.answer(text)

# =========================================
# DEPLOYMENTS
# =========================================

@dp.message(Command("deployments"))
async def deployments(message: Message):

    account = await get_active_account(
        message.from_user.id
    )

    if not account:

        return await message.answer(
            "No active account"
        )

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "GET",
        "/deployments"
    )

    if status != 200:

        return await message.answer(
            "API error"
        )

    deployments = data.get(
        "deployments",
        []
    )

    if not deployments:

        return await message.answer(
            "No deployments found"
        )

    text = "<b>Deployments</b>\n\n"

    for dep in deployments[:10]:

        text += (
            f"ID: <code>{dep['id']}</code>\n"
            f"Status: {dep.get('status')}\n"
            f"Created: {dep.get('created_at')}\n\n"
        )

    await message.answer(text)

# =========================================
# RESTART
# =========================================

@dp.message(Command("restart"))
async def restart(message: Message):

    args = message.text.split()

    if len(args) < 2:

        return await message.answer(
            "Usage:\n/restart SERVICE_ID"
        )

    service_id = args[1]

    account = await get_active_account(
        message.from_user.id
    )

    if not account:
        return

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "POST",
        f"/services/{service_id}/restart"
    )

    if status not in [200, 202]:

        return await message.answer(
            f"Failed:\n{data}"
        )

    await message.answer(
        "✅ Restart triggered"
    )

# =========================================
# REDEPLOY
# =========================================

@dp.message(Command("redeploy"))
async def redeploy(message: Message):

    args = message.text.split()

    if len(args) < 2:

        return await message.answer(
            "Usage:\n/redeploy SERVICE_ID"
        )

    service_id = args[1]

    account = await get_active_account(
        message.from_user.id
    )

    if not account:
        return

    token = account["token"]

    status, data = await koyeb_request(
        token,
        "POST",
        f"/services/{service_id}/redeploy"
    )

    if status not in [200, 202]:

        return await message.answer(
            f"Failed:\n{data}"
        )

    await message.answer(
        "🚀 Redeploy triggered"
    )

# =========================================
# MAIN
# =========================================

async def main():

    print("Bot started")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
