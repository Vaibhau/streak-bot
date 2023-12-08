import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict
import aiogram

from sqlalchemy import BigInteger, String, delete, desc, asc, Boolean
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Update,
    BotCommand
)
import datetime
from aiogram import F
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())


class Base(DeclarativeBase):
    pass


class Groups(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger())
    group_id: Mapped[int] = mapped_column(BigInteger())
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)


class Users(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger())
    name: Mapped[str] = mapped_column(String())
    username: Mapped[str] = mapped_column(String(), nullable=True)
    streak: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    attempts: Mapped[int]
    maximum_days: Mapped[int] = mapped_column(BigInteger())
    all_days: Mapped[int] = mapped_column(BigInteger())


POSTGRES_LOGIN = os.getenv("POSTGRES_LOGIN")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_DB = os.getenv("POSTGRES_DB")
SQLALCHEMY_ECHO = True if os.getenv("SQLALCHEMY_ECHO") == "true" else False
TIMEOUT_SCOREBOARD_IN_SECONDS = int(os.getenv("TIMEOUT_SCOREBOARD_IN_SECONDS") or 180)
TOKEN = os.getenv("TOKEN")
BASE_REPO = os.getenv("BASE_REPO") or "https://github.com/Aqendo/streak-bot"
SHOW_BASE_REPO_IN_HELP = (
    True if os.getenv("SHOW_BASE_REPO_IN_HELP") == "true" else False
)
engine = create_async_engine(
    f"postgresql+asyncpg://{POSTGRES_LOGIN}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}/{POSTGRES_DB}",
    echo=SQLALCHEMY_ECHO,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


router = Router()
pool = None
scoreboards = {}
dp = Dispatcher()
dp.include_router(router)


async def create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Users.metadata.create_all)
        await conn.run_sync(Groups.metadata.create_all)


@dp.update.outer_middleware()
async def database_transaction_middleware(
    handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
    event: Update,
    data: Dict[str, Any],
) -> Any:
    event_old = event
    event = event.callback_query or event.message
    if event is None:
        return
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == event.from_user.id)
        )
        session_result = session_result.scalar()
        if session_result is not None and (
            session_result.name != event.from_user.full_name
            or session_result.username != event.from_user.username
        ):
            session_result.name = event.from_user.full_name
            session_result.username = event.from_user.username
            session.add(session_result)
            await session.commit()
    return await handler(event_old, data)


@router.message(Command(commands=["start"]))
async def command_start_handler(message: Message) -> None:
    await message.answer(f"Hello, <b>{message.from_user.full_name}!</b>")


@router.message(Command(commands=["enablescoreboard", "enableScoreboard"]))
async def enablescoreboard_handler(message: Message) -> None:
    if message.chat.id == message.from_user.id:
        await message.reply("🚫 You can't enable scoreboards in private chat.")
        return
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == message.from_user.id)
        )
        session_result = session_result.scalar()
        if session_result is None:
            await message.reply("↪️ Use /streak to start a new streak.")
            return
        session_result = await session.execute(
            select(Groups).where(
                Groups.user_id == message.from_user.id,
                Groups.group_id == message.chat.id,
            )
        )
        session_result = session_result.scalar()
        if session_result is None:
            session.add(Groups(user_id=message.from_user.id, group_id=message.chat.id))
            await session.commit()
            await message.reply(
                "✅ You are now appearing on the scoreboard.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Turn this message into a scoreboard",
                                callback_data=f"turn_{message.from_user.id}",
                            )
                        ]
                    ]
                ),
            )
        else:
            await message.reply(
                "🚫 You already enabled scoreboards.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Turn this message into a scoreboard",
                                callback_data=f"turn_{message.from_user.id}",
                            )
                        ]
                    ]
                ),
            )


@router.message(Command(commands=["streak"]))
async def register_handler(message: Message) -> None:
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == message.from_user.id)
        )
        session_result = session_result.scalar()
        if session_result is None:
            session.add(
                Users(
                    user_id=message.from_user.id,
                    name=message.from_user.full_name,
                    username=message.from_user.username,
                    streak=datetime.datetime.now(),
                    attempts=1,
                    maximum_days=0,
                    all_days=0,
                )
            )
            await session.commit()
            await message.reply("Streak has been started! You have 0 days!")
        else:
            days = (datetime.datetime.now() - session_result.streak).days
            days_str = "days" if days != 1 else "day"
            await message.answer(
                f"Hey {message.from_user.full_name}.\n🔥 Your streak is {days} {days_str} long.\n\n❌ Use /relapse if you have relapsed.",
                parse_mode=None,
            )


@router.message(Command(commands=["stats"]))
async def stats_handler(message: Message) -> None:
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == message.from_user.id)
        )
        session_result = session_result.scalar()
        if session_result is None:
            await message.reply("↪️ Use /streak to start a new streak.")
            return
        attempts = session_result.attempts
        days = (datetime.datetime.now() - session_result.streak).days

        days_text = str(attempts) + (
            "th"
            if 4 <= attempts % 100 <= 20
            else {1: "st", 2: "nd", 3: "rd"}.get(attempts % 10, "th")
        )
        message_stats = await message.reply(
            f"""Hey {message.from_user.full_name}, these are your stats.

📅 You went {session_result.all_days + days} days without relapsing
⚡️ Your highest streak is {session_result.maximum_days} days
💂 This is your {days_text} attempt
🔥 Your current streak is {days} days long"""
        )
        await asyncio.sleep(20)
        await message_stats.delete()


@router.message(Command(commands=["deleteAllDataAboutMe", "deletealldataaboutme"]))
async def deleteAllDataAboutMe_handler(message: Message) -> None:
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == message.from_user.id)
        )
        session_result = session_result.scalar()
        if session_result is None:
            await message.reply(
                "I don't have any info about you at the moment. You can register a streak with /streak command."
            )
            return
        await message.reply(
            "Are you sure you want to delete <b>ALL</b> data about yourself? This is <b>IRREVERSIBLE</b> and no one on the entire planet Earth will be able to restore your streaks!",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Yes",
                            callback_data=f"remove_{message.from_user.id}",
                        ),
                        InlineKeyboardButton(
                            text="No",
                            callback_data=f"cancel_{message.from_user.id}",
                        ),
                    ]
                ]
            ),
        )


@router.message(Command(commands=["relapse"]))
async def relapse_handler(message: Message) -> None:
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == message.from_user.id)
        )
        session_result = session_result.scalar()
        if session_result is None:
            await message.reply("To start a streak, write /streak")
            return
        await message.reply(
            "Are you sure you want to register a <b>relapse</b>?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Yes",
                            callback_data=f"relapse_{message.from_user.id}",
                        ),
                        InlineKeyboardButton(
                            text="No",
                            callback_data=f"cancel_{message.from_user.id}",
                        ),
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("cancel_"))
async def cancel_relapse(callback_query: CallbackQuery) -> None:
    if callback_query.from_user.id != int(callback_query.data.split("_", 1)[1]):
        await callback_query.answer("🚫 This button was not meant for you")
        return
    await callback_query.message.edit_text("🆗 Cancelled.")


@router.message(Command(commands=["help"]))
async def help(message: Message) -> None:
    message_to_reply = """/streak - 🍀 start a new streak
/relapse - 🗑 relapse a streak
/enableScoreboard - ✅  make your account show up on the scoreboard
/setStreak <daysCount> - ⚙️ set a custom streak
/stats - 📊 display some statistics 
/check <id/username> - 🔧  deletes account from scoreboard if it's been deleted
/deleteAllDataAboutMe - 🗑 Delete all data about yourself
/removeFromLeaderboard <id/username> - 🗑 Remove user from leaderboard of this group (admin-only!)
/returnToLeaderboard <id/username> - 🗑 Return user to leaderboard of this group, if it's banned (admin-only!)
"""
    if SHOW_BASE_REPO_IN_HELP:
        message_to_reply += (
            """\nIf you like this bot and want to support development, consider giving this project a star on GitHub:
"""
            + BASE_REPO
        )
    await message.reply(
        message_to_reply,
        disable_web_page_preview=True,
        parse_mode=None
    )

@router.message(Command(commands=["removeFromLeaderboard", "removefromleaderboard"]))
async def check(message: Message, bot: Bot, command: CommandObject) -> None:
    if message.chat.id == message.from_user.id:
        await message.reply("❌ This command works only in groups.")
        return
    if not command.args:
        await message.reply(
            "❌ Not enough arguments\.\n**USAGE**:\n`/removeFromLeaderboard \<\+id/\+username\>`\n\n_Deletes an account from scoreboard \(admins only\)_",
            parse_mode="MarkdownV2",
        )
        return
    user_to_delete = command.args
    async with async_session() as session:
        if not user_to_delete.isnumeric():
            user_result = await session.scalar(
                select(Users).where(Users.username == user_to_delete.strip("@"))
            )
            if not user_result:
                await message.reply("This user never used me")
                return
            user_to_delete = user_result.user_id
        user_to_delete = int(user_to_delete)
        user = await session.scalar(select(Groups).where(Groups.group_id == message.chat.id, Groups.user_id == user_to_delete))
        user.is_banned = True
        try:
            user_who_deletes = await bot.get_chat_member(message.chat.id, message.from_user.id)
        except:
            await message.reply("I can't get chat admins! Please report this error to the support group.")
            return
        if not user_who_deletes.can_restrict_members and user_who_deletes.status != 'creator':
            await message.reply(str(user_who_deletes))
            await message.reply("This command is admin-only (with ability to restrict members)!")
            return
        session.add(user)
        await session.commit()
        await message.answer(
            f"Succesfully removed account with id {user_to_delete} from scoreboard of this group."
        )

@router.message(Command(commands=["returnToLeaderboard", "returntoleaderboard"]))
async def check(message: Message, bot: Bot, command: CommandObject) -> None:
    if message.chat.id == message.from_user.id:
        await message.reply("❌ This command works only in groups.")
        return
    if not command.args:
        await message.reply(
            "❌ Not enough arguments\.\n**USAGE**:\n`/returnToLeaderboard \<\+id/\+username\>`\n\Returns an account to scoreboard if it's banned \(admins only\)_",
            parse_mode="MarkdownV2",
        )
        return
    user_to_delete = command.args
    async with async_session() as session:
        if not user_to_delete.isnumeric():
            user_result = await session.scalar(
                select(Users).where(Users.username == user_to_delete.strip("@"))
            )
            if not user_result:
                await message.reply("This user never used me")
                return
            user_to_delete = user_result.user_id
        user_to_delete = int(user_to_delete)
        user = await session.scalar(select(Groups).where(Groups.group_id == message.chat.id, Groups.user_id == user_to_delete))
        user.is_banned = False
        try:
            user_who_deletes = await bot.get_chat_member(message.chat.id, message.from_user.id)
        except:
            await message.reply("I can't get chat admins! Please report this error to the support group.")
            return
        if not user_who_deletes.can_restrict_members and user_who_deletes.status != 'creator':
            await message.reply(str(user_who_deletes))
            await message.reply("This command is admin-only (with ability to restrict members)!")
            return
        session.add(user)
        await session.commit()
        await message.answer(
            f"Succesfully returned an account with id {user_to_delete} to scoreboard of this group."
        )


@router.message(Command(commands=["check"]))
async def check(message: Message, bot: Bot, command: CommandObject) -> None:
    if not command.args:
        await message.reply(
            "❌ Not enough arguments\.\n**USAGE**:\n`/check \<\+id/\+username\>`\n\n_Deletes an account from scoreboard if it's been deleted_",
            parse_mode="MarkdownV2",
        )
        return
    user_to_delete = command.args
    async with async_session() as session:
        if not user_to_delete.isnumeric():
            user_result = await session.scalar(
                select(Users).where(Users.username == user_to_delete.strip("@"))
            )
            if not user_result:
                await message.reply("This user never used me")
                return
            user_to_delete = user_result.user_id
        else:
            user_result = await session.scalar(
                select(Users).where(Users.user_id == int(user_to_delete))
            )
            if not user_result:
                await message.reply("This user never used me")
                return
            user_to_delete = int(user_to_delete)
        member = None
        try:
            member = await bot.get_chat_member(message.chat.id, user_to_delete)
        except:
            await message.reply(
                "Query failed. If you sure you entered right ID, then wait some time to Telegram to wake up from maintenance or something."
            )
            return
        if not member:
            return
        if member.user.first_name != "" and member.user.first_name != "Deleted Account":
            await message.reply(
                "This user is alive and hasn't deleted his account yet."
            )
            return
        await session.delete(user_result)
        await session.execute(delete(Groups).where(Groups.user_id == user_to_delete))
        await session.commit()
        await message.answer(
            f"Succesfully removed account with id {user_to_delete} from my database."
        )


@router.callback_query(F.data.startswith("relapse_"))
async def register_a_relapse(callback_query: CallbackQuery) -> None:
    if callback_query.from_user.id != int(callback_query.data.split("_", 1)[1]):
        await callback_query.answer("🚫 This button was not meant for you")
        return
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == callback_query.from_user.id)
        )
        session_result = session_result.scalar()
        days = (datetime.datetime.now() - session_result.streak).days
        if days > session_result.maximum_days:
            session_result.maximum_days = days
        session_result.all_days += days
        session_result.streak = datetime.datetime.now()
        session_result.attempts += 1
        session.add(session_result)
        await session.commit()
        message_relapse = await callback_query.message.edit_text(
            f"""🗑 Sad to see your streak of {days} days go down the drain.

I started a new streak for you.

🍀 Good luck, {callback_query.from_user.full_name}, you will need it.

👉🏻 Check the <a href='https://easypeasymethod.org/'>easypeasy</a> method, it might help you.""",
            disable_web_page_preview=True,
        )
        await asyncio.sleep(10)
        await message_relapse.delete()


@router.callback_query(F.data.startswith("remove_"))
async def remove_all_data_logic(callback_query: CallbackQuery) -> None:
    if callback_query.from_user.id != int(callback_query.data.split("_", 1)[1]):
        await callback_query.answer("🚫 This button was not meant for you")
        return
    async with async_session() as session:
        deletion_result_users = await session.execute(
            delete(Users).where(Users.user_id == callback_query.from_user.id)
        )
        deletion_result_groups = await session.execute(
            delete(Groups).where(Groups.user_id == callback_query.from_user.id)
        )
        await session.commit()
        await callback_query.message.edit_text(
            f"""🗑 From now I know nothing about you! All your data was erased forever. If you want to start again, just use /streak command.""",
        )


async def scoreboard(callback_query: CallbackQuery) -> None:
    async with async_session() as session:
        session_result = await session.execute(
            select(Users)
            .join(Groups, Users.user_id == Groups.user_id)
            .filter(Groups.group_id == callback_query.message.chat.id, Groups.is_banned == False)
            .order_by(asc(Users.streak))
            .limit(50)
        )
        old_message = ""
        message_result = "🏆 Scoreboard\n\n"
        for id, users_tuple in enumerate(session_result.all()):
            username = users_tuple[0].username
            name_text = users_tuple[0].name
            username_text = ""
            days = (datetime.datetime.now() - users_tuple[0].streak).days
            if username is not None:
                username_text = " (@" + username + ")"
            else:
                name_text = (
                    f"<a href='tg://user?id={users_tuple[0].user_id}'>{name_text}</a>"
                )

            message_result += "%d. %s %s — <b>%d %s</b>\n" % (
                id + 1,
                name_text,
                username_text,
                days,
                "days" if days != 1 else "day",
            )
        try:
            await callback_query.message.edit_text(message_result)
        except aiogram.exceptions.TelegramBadRequest:
            return


@router.callback_query(F.data.startswith("turn_"))
async def turn_scoreboard(callback_query: CallbackQuery) -> None:
    if callback_query.from_user.id != int(callback_query.data.split("_", 1)[1]):
        await callback_query.answer("🚫 This button was not meant for you")
        return
    scoreboards[callback_query.chat_instance] = callback_query.message
    while scoreboards[callback_query.chat_instance] == callback_query.message:
        await scoreboard(callback_query)
        await asyncio.sleep(TIMEOUT_SCOREBOARD_IN_SECONDS)


@router.message(Command(commands=["setstreak", "setStreak"]))
async def set_streak(message: Message) -> None:
    days = message.text.split(" ", 1)[-1]
    if not days.isnumeric() or int(days) > 100000:
        return
    days = int(days)
    async with async_session() as session:
        session_result = await session.execute(
            select(Users).where(Users.user_id == message.from_user.id)
        )
        session_result = session_result.scalar()
    if session_result is None:
        await message.answer("↪️ Use /streak to start a new streak.")
        return
    session_result.streak = datetime.datetime.now() - datetime.timedelta(days=days)
    session.add(session_result)
    await session.commit()
    await message.answer("Done")


async def main() -> None:
    await create_all()
    logging.info("created")
    bot = Bot(TOKEN, parse_mode="HTML")
    await dp.start_polling(bot)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    logging.basicConfig(level=logging.INFO)
    loop.run_until_complete(main())
