import os
import re
import json
import math
import time
import psutil
import aria2p
import pickle
import logging
import requests
import humanize
import subprocess
import threading
import qbittorrentapi
from qbit_conf import QBIT_CONF
from io import StringIO
from typing import Union, Optional
from dotenv import load_dotenv
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
from telegram import Update, error, constants, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext.filters import Chat
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, Application
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('log.txt'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
aria2c: aria2p.API = None
CONFIG_FILE_URL = os.getenv("CONFIG_FILE_URL")
BOT_START_TIME = None
BOT_TOKEN = None
GDRIVE_FOLDER_ID = None
AUTHORIZED_USERS = set()
PICKLE_FILE_NAME = "token.pickle"
START_CMD = "start"
MIRROR_CMD = "aria"
STATUS_CMD = "status"
INFO_CMD = "info"
LOG_CMD = "log"
QBIT_CMD = "qbit"
GDRIVE_BASE_URL = "https://drive.google.com/uc?id={}&export=download"
GDRIVE_FOLDER_BASE_URL = "https://drive.google.com/drive/folders/{}"
TRACKER_URLS = [
    "https://cdn.staticaly.com/gh/XIU2/TrackersListCollection/master/all_aria2.txt",
    "https://raw.githubusercontent.com/hezhijie0327/Trackerslist/main/trackerslist_tracker.txt"
]
ARIA_COMMAND = "aria2c --allow-overwrite=true --auto-file-renaming=true --check-certificate=false --check-integrity=true "\
               "--continue=true --content-disposition-default-utf8=true --daemon=true --disk-cache=40M --enable-rpc=true "\
               "--force-save=true --http-accept-gzip=true --max-connection-per-server=16 --max-concurrent-downloads=10 "\
               "--max-file-not-found=0 --max-tries=20 --min-split-size=10M --optimize-concurrent-downloads=true --reuse-uri=true "\
               "--quiet=true --rpc-max-request-size=1024M --split=10 --summary-interval=0 --user-agent=Wget/1.12 --seed-time=0 "\
               "--bt-enable-lpd=true --bt-detach-seed-only=true --bt-remove-unselected-file=true --follow-torrent=mem --bt-tracker={}"
MAGNET_REGEX = r"magnet:\?xt=urn:btih:[a-zA-Z0-9]*"
URL_REGEX = r"(?:(?:https?|ftp):\/\/)?[\w/\-?=%.]+\.[\w/\-?=%.]+"
DOWNLOAD_PATH = "/usr/src/app/downloads"
ARIA_OPTS = {'dir': DOWNLOAD_PATH.rstrip("/"), 'max-upload-limit': '2M'}
GDRIVE_PERM = {
    'role': 'reader',
    'type': 'anyone',
    'value': None,
    'withLink': True
}

def get_qbit_client() -> Optional[qbittorrentapi.Client]:
    try:
        return qbittorrentapi.Client(
            host='http://localhost',
            port=8090,
            username='admin',
            password='adminadmin',
            VERIFY_WEBUI_CERTIFICATE=False,
            REQUESTS_ARGS={'timeout': (30, 60)},
            DISABLE_LOGGING_DEBUG_OUTPUT=True
        )
    except (qbittorrentapi.exceptions.APIConnectionError,
            qbittorrentapi.exceptions.LoginFailed,
            qbittorrentapi.exceptions.Forbidden403Error) as err:
        logger.error(f"Failed to initialize client: {str(err)}")
        return None

def send_status_update(msg: str) -> None:
    tg_api_url = "https://api.telegram.org/bot{}/sendMessage"
    headers = {
        "accept": "application/json",
        "User-Agent": "Telegram Bot SDK - (https://github.com/irazasyed/telegram-bot-sdk)",
        "content-type": "application/json"
    }
    for user_id in AUTHORIZED_USERS:
        payload = {
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": False,
            "reply_to_message_id": None,
            "chat_id": user_id
        }
        try:
            response = requests.post(tg_api_url.format(BOT_TOKEN), json=payload, headers=headers)
            if response.ok:
                logger.info(f"Status msg sent to: {user_id}")
            response.close()
        except requests.exceptions.RequestException:
            logger.error(f"Failed to send updates to {user_id}")

def get_oauth_creds():
    logger.info("Loading token.pickle file")
    with open(PICKLE_FILE_NAME, 'rb') as f:
        credentials = pickle.load(f)
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError:
                logger.error("Failed to refresh token")
                return None
        return credentials

def count_uploaded_files(creds, folder_id: str) -> int:
    count = 0
    logger.info(f"Getting the count of files present in {folder_id}")
    try:
        gdrive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        files_list = gdrive_service.files().list(
            supportsAllDrives=True, includeItemsFromAllDrives=True, corpora='allDrives',
            q=f"mimeType != 'application/vnd.google-apps.folder' and trashed=false and parents in '{folder_id}'",
            spaces='drive', fields='files(id, name, size)').execute()["files"]
        gdrive_service.close()
        count = len(files_list)
    except Exception:
        logger.error(f"Failed to get details of {folder_id}")
    return count

def create_folder(folder_name: str, creds) -> Optional[str]:
    folder_id = None
    logger.info(f"Creating folder: {folder_name} in GDrive")
    dir_metadata = {'name': folder_name, 'parents': [GDRIVE_FOLDER_ID], 'mimeType': 'application/vnd.google-apps.folder'}
    try:
        gdrive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        upload_dir = gdrive_service.files().create(body=dir_metadata, supportsAllDrives=True, fields='id').execute()
        folder_id = upload_dir.get('id')
    except Exception as err:
        logger.error(f"Failed to create dir: {folder_name} error: {str(err)}")
    else:
        logger.info(f"Setting permissions for: {folder_name}")
        try:
            gdrive_service.permissions().create(fileId=folder_id, body=GDRIVE_PERM, supportsAllDrives=True).execute()
            gdrive_service.close()
        except HttpError:
            logger.warning(f"Failed to set permission for: {folder_name}")
    return folder_id

@retry(wait=wait_exponential(multiplier=2, min=3, max=6), stop=stop_after_attempt(3), retry=(retry_if_exception_type(Exception)))
def upload_file(file_path: str, folder_id: str, creds) -> None:
    file_name = os.path.basename(file_path)
    file_metadata = {'name': file_name, 'parents': [folder_id]}
    logger.info(f"Starting upload: {file_name}")
    gdrive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
    drive_file = gdrive_service.files().create(
        body=file_metadata, supportsAllDrives=True,
        media_body=MediaFileUpload(filename=file_path, resumable=True),
        fields='id')
    response = None
    while response is None:
        try:
            _status, response = drive_file.next_chunk()
        except HttpError as err:
            if err.resp.get('content-type', '').startswith('application/json'):
                message = eval(err.content).get('error').get('errors')[0].get('message')
            else:
                message = err.error_details
            logger.warning(f"Retrying upload: {file_name} Reason: {message}")
            raise err
    logger.info(f"Upload completed for {file_name}")
    drive_file = gdrive_service.files().get(fileId=response['id'], supportsAllDrives=True).execute()
    logger.info(f"Setting permissions for {file_name}")
    try:
        gdrive_service.permissions().create(fileId=drive_file.get('id'), body=GDRIVE_PERM, supportsAllDrives=True).execute()
        gdrive_service.close()
    except HttpError:
        pass
    if folder_id == GDRIVE_FOLDER_ID:
        send_status_update(f"🗂️ <b>File:</b> <code>{file_name}</code> uploaded ✔️\n🌐 <b>Link:</b> {GDRIVE_BASE_URL.format(drive_file.get('id'))}")

def upload_to_gdrive(api: aria2p.API = None, gid: str = None, hash: str = None) -> None:
    count = 0
    creds = None
    folder_id = None
    is_dir = False
    down = None
    file_path = None
    file_name = None
    logger.info("Download complete event triggered") if hash is None else logger.info(f"Uploading qbit file: {hash}")
    try:
        if hash is not None:
            if qb_client := get_qbit_client():
                file_name = qb_client.torrents_info(torrent_hashes=[hash])[0].name
                file_path = f"{DOWNLOAD_PATH}/{file_name}"
                qb_client.auth_log_out()
        else:
            down = aria2c.get_download(gid)
            file_name = down.name
            file_path = f"{DOWNLOAD_PATH}/{file_name}"
        if hash is None and down.followed_by_ids:
            logger.info(f"Skip uploading of: {file_name}")
        else:
            if creds := get_oauth_creds():
                if os.path.isdir(file_path) is True:
                    is_dir = True
                    if folder_id := create_folder(os.path.basename(file_path), creds):
                        for path, currentDirectory, files in os.walk(file_path):
                            for f in files:
                                count += 1
                                upload_file(os.path.join(path, f), folder_id, creds)
                else:
                    upload_file(file_path, GDRIVE_FOLDER_ID, creds)
    except RetryError as err:
        if isinstance(err.last_attempt.exception(), HttpError):
            reason = str(err.last_attempt.exception().error_details)
        else:
            reason = str(err)
        msg = f"🗂️ <b>File:</b> <code>{file_name}</code> upload <b>failed</b>❗\n⚠️ <b>Reason:</b> <code>{reason.replace('>', '').replace('<', '')}</code>"
        logger.warning(f"Upload failed for: {file_name} Total attempts: {err.last_attempt.attempt_number}")
        if is_dir is False:
            send_status_update(msg)
    except (aria2p.ClientException, OSError, AttributeError):
        logger.error("Failed to complete download event task")
    if is_dir is True:
        if count == count_uploaded_files(creds, folder_id):
            send_status_update(f"🗂️ <b>Folder: </b><code>{file_name}</code> <b>uploaded</b> ✔️\n🌐 <b>Link: <b><code>{GDRIVE_FOLDER_BASE_URL.format(folder_id)}</code>")
        else:
            send_status_update(f"🗂️ <b>Folder: </b><code>{file_name}</code> upload <b>failed</b>❗\n⚠️ <i>Please check the log for more details using</i> <code>/{LOG_CMD}</code>")

def get_user(update: Update) -> Union[str, int]:
    return update.message.from_user.name if update.message.from_user.name is not None else update.message.chat_id

def get_download_info(down: aria2p.Download) -> str:
    info = f"🗂 <b>Filename:</b> <code>{down.name}</code>\n🚦 <b>Status:</b> <code>{down.status}</code>\n📀 <b>Size:</b> <code>{down.total_length_string()}</code>\n"\
            f"📥 <b>Downloaded:</b> <code>{down.completed_length_string()} ({down.progress_string()})</code>\n🧩 <b>Peers:</b> <code>{down.connections}</code>\n"\
            f"⚡ <b>Speed:</b> <code>{down.download_speed_string()}</code>\n⏳ <b>ETA:</b> <code>{down.eta_string()}</code>"
    if down.bittorrent is not None:
        info += f"\n🥑 <b>Seeders:</b> <code>{down.num_seeders}</code>"
    info += "\n⚙️ <b>Engine: </b><code>Aria2</code>"
    return info

def get_qbit_info(hash: str, client: qbittorrentapi.Client = None) -> str:
    info = ''
    for torrent in client.torrents_info(torrent_hashes=[hash]):
        info += f"🗂 <b>Filename:</b> <code>{torrent.name}</code>\n🚦 <b>Status:</b> <code>{torrent.state_enum.value}</code>\n📀 <b>Size:</b> <code>{humanize.naturalsize(torrent.total_size)}</code>\n"\
            f"📥 <b>Downloaded:</b> <code>{humanize.naturalsize(torrent.downloaded)} ({round(number=torrent.progress * 100, ndigits=2)}%)</code>\n📦 <b>Remaining: </b><code>{humanize.naturalsize(torrent.amount_left)}</code>\n"\
            f"🧩 <b>Peers:</b> <code>{torrent.num_leechs}</code>\n🥑 <b>Seeders:</b> <code>{torrent.num_seeds}</code>\n⚡ <b>Speed:</b> <code>{humanize.naturalsize(torrent.dlspeed)}/s</code>\n"\
            f"⏳ <b>ETA:</b> <code>{humanize.naturaldelta(torrent.eta)}</code>\n⚙️ <b>Engine: </b><code>Qbittorent</code>"
    return info

def get_downloads_count() -> int:
    count = 0
    try:
        count += len(aria2c.get_downloads())
        if qb_client := get_qbit_client():
            count += len(qb_client.torrents_info())
            qb_client.auth_log_out()
    except Exception:
        logger.error("Failed to get total count of download tasks")
    return count

def get_keyboard(down: aria2p.Download) -> InlineKeyboardMarkup:
    action_btn = [InlineKeyboardButton(text=f"🔆 Show All ({get_downloads_count()})", callback_data=f"aria-lists")]
    if "error" in down.status:
        action_btn.append(InlineKeyboardButton(text="🚀 Retry", callback_data=f"aria-retry#{down.gid}"))
    elif "paused" in down.status:
        action_btn.append(InlineKeyboardButton(text="▶ Resume", callback_data=f"aria-resume#{down.gid}"))
    elif "active" in down.status:
        action_btn.append(InlineKeyboardButton(text="⏸ Pause", callback_data=f"aria-pause#{down.gid}"))
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="♻ Refresh", callback_data=f"aria-refresh#{down.gid}"),
          InlineKeyboardButton(text="🚫 Delete", callback_data=f"aria-remove#{down.gid}")], action_btn]
    )

def get_qbit_keyboard(qbit: qbittorrentapi.TorrentDictionary = None) -> InlineKeyboardMarkup:
    action_btn = [InlineKeyboardButton(text=f"🔆 Show All ({get_downloads_count()})", callback_data=f"qbit-lists")]
    if qbit.state_enum.is_errored:
        action_btn.append(InlineKeyboardButton(text="🚀 Retry", callback_data=f"qbit-retry#{qbit.hash}"))
    elif "pausedDL" == qbit.state_enum.value:
        action_btn.append(InlineKeyboardButton(text="▶ Resume", callback_data=f"qbit-resume#{qbit.hash}"))
    elif qbit.state_enum.is_downloading:
        action_btn.append(InlineKeyboardButton(text="⏸ Pause", callback_data=f"qbit-pause#{qbit.hash}"))
    elif qbit.state_enum.is_complete or "pausedUP" == qbit.state_enum.value:
        action_btn.append(InlineKeyboardButton(text="☁️ Upload", callback_data=f"qbit-upload#{qbit.hash}"))
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="♻ Refresh", callback_data=f"qbit-refresh#{qbit.hash}"),
          InlineKeyboardButton(text="🚫 Delete", callback_data=f"qbit-remove#{qbit.hash}")], action_btn]
    )

async def reply_message(msg: str, update: Update,
                        context: ContextTypes.DEFAULT_TYPE,
                        keyboard: InlineKeyboardMarkup = None, reply: bool = True) -> None:
    try:
        await context.bot.send_message(
            text=msg,
            chat_id=update.message.chat_id,
            reply_to_message_id=update.message.id if reply is True else None,
            reply_markup=keyboard,
            parse_mode=constants.ParseMode.HTML,
        )
    except AttributeError:
        logger.error("Failed to send reply")
    except error.TelegramError as err:
        logger.error(f"Failed to reply for: {update.message.text} to: {get_user(update)} error: {str(err)}")

async def edit_message(msg: str, callback: CallbackQuery, keyboard: InlineKeyboardMarkup=None) -> None:
    try:
        await callback.edit_message_text(
            text=msg,
            parse_mode=constants.ParseMode.HTML,
            reply_markup=keyboard
        )
    except error.TelegramError:
        logger.error(f"Failed to edit message for: {callback.data}")

async def get_aria_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_btns = []
    msg = ''
    keyboard = None
    for down in aria2c.get_downloads():
        file_btns.append([InlineKeyboardButton(text=f"[{down.status}] {down.name}", callback_data=f"aria-file#{down.gid}")])
    if qb_client := get_qbit_client():
        for torrent in qb_client.torrents_info():
            file_btns.append([InlineKeyboardButton(text=f"[{torrent.state_enum.value}] {torrent.name}", callback_data=f"qbit-file#{torrent.hash}")])
        qb_client.auth_log_out()
    if file_btns:
        msg += f"🗂️ <b>Downloads ({len(file_btns)})</b>"
        keyboard = InlineKeyboardMarkup(file_btns)
    else:
        msg += "🔅 <b>No downloads found !</b>"
    if update.callback_query is not None:
        await edit_message(msg, update.callback_query, keyboard)
    else:
        await reply_message(msg, update, context, keyboard, False)

async def aria_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.callback_query.answer()
        callback_data = update.callback_query.data.split("#", maxsplit=1)
        action = callback_data[0].strip()
        if "aria" in action:
            aria_obj = aria2c.get_download(callback_data[1].strip()) if "lists" not in action else None
            if action in ["aria-refresh", "aria-file", "aria-pause", "aria-resume"]:
                if "pause" in action:
                    aria2c.pause(downloads=[aria_obj], force=True)
                elif "resume" in action:
                    aria2c.resume(downloads=[aria_obj])
                await edit_message(get_download_info(aria_obj.live), update.callback_query, get_keyboard(aria_obj))
            elif action in ["aria-retry", "aria-remove", "aria-lists"]:
                if "retry" in action:
                    aria2c.retry_downloads(downloads=[aria_obj], clean=False)
                elif "remove" in action:
                    aria2c.remove(downloads=[aria_obj], force=True, files=True, clean=True)
                await get_aria_downloads(update, context)
        else:
            torrent_hash = callback_data[1].strip() if len(callback_data) > 1 else None
            if qb_client := get_qbit_client():
                if action in ["qbit-refresh", "qbit-file", "qbit-pause", "qbit-resume"]:
                    if "pause" in action:
                        qb_client.torrents_pause(torrent_hashes=[torrent_hash])
                    elif "resume" in action:
                        qb_client.torrents_resume(torrent_hashes=[torrent_hash])
                    await edit_message(get_qbit_info(torrent_hash, qb_client), update.callback_query, get_qbit_keyboard(qb_client.torrents_info(torrent_hashes=[torrent_hash])[0]))
                elif action in ["qbit-retry", "qbit-remove", "qbit-lists"]:
                    if "retry" in action:
                        qb_client.torrents_set_force_start(enable=True, torrent_hashes=[torrent_hash])
                    elif "remove" in action:
                        qb_client.torrents_delete(delete_files=True, torrent_hashes=[torrent_hash])
                    await get_aria_downloads(update, context)
                elif "upload" in action:
                    threading.Thread(target=upload_to_gdrive, kwargs={'hash': torrent_hash}).start()
                    logger.info(f"Upload thread started for: {torrent_hash}")
                    await context.bot.send_message(
                        chat_id=update.callback_query.message.chat_id,
                        text=f"🌈 <b>Upload started for: </b><code>{qb_client.torrents_info(torrent_hashes=[torrent_hash])[0].name}</code>\n⚠️ <i>Do not press the upload button again unless the upload has failed</i>",
                        parse_mode=constants.ParseMode.HTML)
                qb_client.auth_log_out()
    except aria2p.ClientException:
        await edit_message(f"⁉️ <b>Unable to find GID:</b><code>{update.callback_query.data}</code>",
                           update.callback_query, InlineKeyboardMarkup([[InlineKeyboardButton(text="", callback_data="")]]))
    except (error.TelegramError, requests.exceptions.RequestException, IndexError, ValueError):
        logger.error(f"Failed to answer callback for: {update.callback_query.data}")

async def get_sys_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    details = f"<b>CPU Usage :</b> {psutil.cpu_percent(interval=None)}%\n" \
              f"<b>CPU Freq  :</b> {math.ceil(psutil.cpu_freq(percpu=False).current)} MHz\n" \
              f"<b>CPU Cores :</b> {psutil.cpu_count(logical=True)}\n" \
              f"<b>Total RAM :</b> {humanize.naturalsize(psutil.virtual_memory().total)}\n" \
              f"<b>Used RAM  :</b> {humanize.naturalsize(psutil.virtual_memory().used)}\n" \
              f"<b>Free RAM  :</b> {humanize.naturalsize(psutil.virtual_memory().available)}\n" \
              f"<b>Total Disk:</b> {humanize.naturalsize(psutil.disk_usage('/').total)}\n" \
              f"<b>Used Disk :</b> {humanize.naturalsize(psutil.disk_usage('/').used)}\n" \
              f"<b>Free Disk :</b> {humanize.naturalsize(psutil.disk_usage('/').free)}\n" \
              f"<b>Swap Mem  :</b> {humanize.naturalsize(psutil.swap_memory().used)} of {humanize.naturalsize(psutil.swap_memory().total)}\n" \
              f"<b>Threads   :</b> {threading.active_count()}\n" \
              f"<b>Network IO:</b> 🔻 {humanize.naturalsize(psutil.net_io_counters().bytes_recv)} 🔺 {humanize.naturalsize(psutil.net_io_counters().bytes_sent)}"
    try:
        details += f"\n<b>Bot Uptime:</b> {humanize.naturaltime(time.time() - BOT_START_TIME)}"
    except OverflowError:
        pass
    await reply_message(details, update, context)

async def send_log_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Sending log file to {get_user(update)}")
    try:
        with open('log.txt', 'rb') as f:
            await context.bot.send_document(
                chat_id=update.message.chat_id,
                document=f,
                filename=f.name,
                reply_to_message_id=update.message.message_id)
    except error.TelegramError:
        logger.error(f"Failed to send the log file to {get_user(update)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sender = get_user(update)
    logger.info(f"/{START_CMD} sent by {sender}")
    try:
        await update.get_bot().set_my_commands(
            [(START_CMD, "👽 Start the bot"),
             (MIRROR_CMD, "🗳 Mirror file using Aria2"),
             (QBIT_CMD, "🧲 Mirror file using Qbittorrent"),
             (STATUS_CMD, "📥 Show the task list"),
             (INFO_CMD, "⚙️ Show system info"),
             (LOG_CMD, "📄 Get runtime log file")]
        )
    except (error.TelegramError, RuntimeError):
        logger.error("Failed to set commands")
    await reply_message(
        f"Hi 👋, Welcome to <b>Aria2Gdrive</b> bot. I can mirror files to your GDrive. Please use <code>/{MIRROR_CMD}</code> cmd to send links.",
        update, context
    )

async def aria_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/{MIRROR_CMD} sent by {get_user(update)}")
    aria_obj: aria2p.Download = None
    try:
        link = update.message.text.strip().split(" ", maxsplit=1)[1].strip()
        if bool(re.findall(MAGNET_REGEX, link)) is True:
            aria_obj = aria2c.add_magnet(magnet_uri=link, options=ARIA_OPTS)
        elif bool(re.findall(URL_REGEX, link)) is True:
            aria_obj = aria2c.add_uris(uris=[link], options=ARIA_OPTS)
        else:
            logger.warning(f"Invalid link: {link}")
            await reply_message("⁉️ <b>Invalid link given, please send a valid download link.</b>", update, context)
        if aria_obj is not None:
            if aria_obj.has_failed is False:
                logger.info(f"Download started: {aria_obj.name} with GID: {aria_obj.gid}")
                await reply_message(get_download_info(aria_obj), update, context, get_keyboard(aria_obj), False)
            else:
                logger.error(f"Failed to start download: {link} error: {aria_obj.error_code}")
                await reply_message(f"⚠️ <b>Failed to start download</b>\nError:<code>{aria_obj.error_message}</code> ❗", update, context)
    except IndexError:
        await reply_message("😡 <b>Send a link along with the command</b>❗", update, context)

async def qbit_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/{QBIT_CMD} sent by {get_user(update)}")
    if qb_client := get_qbit_client():
        try:
            link = update.message.text.strip().split(" ", maxsplit=1)[1].strip()
            resp = qb_client.torrents_add(urls=link)
            if resp == "Ok.":
                await reply_message("🧲 <b>Torrent added</b> ✔️", update, context)
            else:
                await reply_message("❗ <b>Failed to add it</b>\n⚠️ <i>Kindly verify the given link and retry</i>", update, context)
        except IndexError:
            await reply_message("😡 <b>Send a link along with the command</b>❗", update, context)
        finally:
            qb_client.auth_log_out()
    else:
        await reply_message("⁉️ <b>Error connecting to qbittorrent, please retry</b>", update, context)

def start_bot() -> None:
    global BOT_START_TIME
    logger.info("Starting the BOT")
    try:
        application: Application = ApplicationBuilder().token(BOT_TOKEN).build()
    except (TypeError, error.InvalidToken):
        logger.error("Failed to initialize bot")
    else:
        BOT_START_TIME = time.time()
        logger.info("Registering commands")
        try:
            start_handler = CommandHandler(START_CMD, start, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            aria_handler = CommandHandler(MIRROR_CMD, aria_upload, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            status_handler = CommandHandler(STATUS_CMD, get_aria_downloads, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            info_handler = CommandHandler(INFO_CMD, get_sys_info, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            log_handler = CommandHandler(LOG_CMD, send_log_file, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            qbit_handler = CommandHandler(QBIT_CMD, qbit_upload, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            callback_handler = CallbackQueryHandler(aria_callback_handler, pattern="^aria|qbit")
            application.add_handlers([start_handler, aria_handler, callback_handler, status_handler, info_handler, log_handler, qbit_handler])
            application.run_polling(drop_pending_updates=True)
        except error.TelegramError as err:
            logger.error(f"Failed to start bot: {str(err)}")

def get_trackers(aria: bool=True) -> str:
    trackers = ''
    logger.info("Fetching trackers list")
    try:
        for index, url in enumerate(TRACKER_URLS):
            track_resp = requests.get(url=url)
            if track_resp.ok:
                if aria is True:
                    if index == 0:
                        trackers += track_resp.text.strip('\n')
                    else:
                        trackers += track_resp.text.replace('\n', ',').rstrip(',')
                else:
                    if index == 0:
                        trackers += track_resp.text.strip('\n').replace(',', '\\n')
                    else:
                        trackers += track_resp.text.rstrip('\n').replace('\n', '\\n')
                track_resp.close()
            else:
                logger.error(f"Failed to get data from tracker link {index}")
    except requests.exceptions.RequestException:
        logger.error("Failed to retrieve trackers")
    return trackers

def start_aria() -> None:
    global aria2c
    trackers = get_trackers()
    logger.info(f"Fetched {len(trackers.split(','))} trackers")
    logger.info("Starting aria2c daemon")
    aria_command_args = ARIA_COMMAND.format(f'"{trackers}"').split(' ')
    try:
        subprocess.run(args=aria_command_args, check=True)
        aria2c = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
        aria2c.listen_to_notifications(threaded=True, on_download_complete=upload_to_gdrive)
        logger.info("aria2c daemon started")
    except (subprocess.CalledProcessError, aria2p.client.ClientException, requests.exceptions.RequestException, OSError):
        logger.error("Failed to start aria2c")
        exit()

def start_qbit() -> None:
    qbit_conf_path = '/usr/src/app/.config'
    logger.info("Initializing qbittorrent-nox")
    qbit_conf_data = QBIT_CONF.replace('{trackers_url}', get_trackers(False))
    os.makedirs(name=f"{qbit_conf_path}/qBittorrent/config", exist_ok=True)
    with open(f'{qbit_conf_path}/qBittorrent/config/qBittorrent.conf', 'w', encoding='utf-8') as conf_file:
        conf_file.write(qbit_conf_data)
    logger.info("Starting qbittorrent-nox daemon")
    try:
        subprocess.run(args=["/usr/bin/qbittorrent-nox", "--daemon", "--webui-port=8090", f"--profile={qbit_conf_path}"], check=True)
        time.sleep(2)
        qb_client = get_qbit_client()
        logger.info(f"qbittorrent version: {qb_client.app.version}")
        qb_client.auth_log_out()
    except (subprocess.CalledProcessError, AttributeError):
        logger.error("Failed to start qbittorrent-nox")
        exit()

def setup_bot() -> None:
    global BOT_TOKEN
    global GDRIVE_FOLDER_ID
    global AUTHORIZED_USERS
    if CONFIG_FILE_URL is not None:
        logger.info("Downloading config file")
        try:
            config_file = requests.get(url=CONFIG_FILE_URL)
        except requests.exceptions.RequestException:
            logger.error("Failed to download config file")
        else:
            if config_file.ok:
                logger.info("Loading config values")
                if load_dotenv(stream=StringIO(config_file.text), override=True):
                    config_file.close()
                    BOT_TOKEN = os.environ['BOT_TOKEN']
                    GDRIVE_FOLDER_ID = os.environ['GDRIVE_FOLDER_ID']
                    try:
                        AUTHORIZED_USERS = json.loads(os.environ['USER_LIST'])
                    except json.JSONDecodeError:
                        logger.error("Failed to parse AUTHORIZED_USERS data")
                    else:
                        logger.info("Downloading token.pickle file")
                        try:
                            pickle_file = requests.get(url=os.environ['PICKLE_FILE_URL'])
                        except requests.exceptions.RequestException:
                            logger.error("Failed to download pickle file")
                        else:
                            if pickle_file.ok:
                                with open(PICKLE_FILE_NAME, 'wb') as f:
                                    f.write(pickle_file.content)
                                pickle_file.close()
                                logger.info("config.env data loaded successfully")
                                start_aria()
                                start_qbit()
                                start_bot()
                            else:
                                logger.error("Failed to get pickle file data")
                else:
                    logger.error("Failed to parse config data")
            else:
                logger.error("Failed to get config data")
    else:
        logger.error("CONFIG_FILE_URL is None")

if __name__ == '__main__':
    setup_bot()
