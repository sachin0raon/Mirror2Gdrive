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
import shutil
import magic
import patoolib
import qbittorrentapi
from patoolib import ArchiveMimetypes
from qbit_conf import QBIT_CONF
from pyngrok import ngrok, conf
from urllib.parse import quote
from io import StringIO
from asyncio import sleep
from typing import Union, Optional, Dict
from dotenv import load_dotenv
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
from telegram import Update, error, constants, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, Document
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
logging.getLogger("pyngrok.ngrok").setLevel(logging.ERROR)
logging.getLogger("pyngrok.process").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)
aria2c: Optional[aria2p.API] = None
CONFIG_FILE_URL = os.getenv("CONFIG_FILE_URL")
BOT_START_TIME: Optional[float] = None
BOT_TOKEN: Optional[str] = None
NGROK_AUTH_TOKEN: Optional[str] = None
GDRIVE_FOLDER_ID: Optional[str] = None
AUTHORIZED_USERS = set()
QBIT_STATUS_DICT = dict()
UPLOAD_MODE_DICT = dict()
PICKLE_FILE_NAME = "token.pickle"
START_CMD = "start"
MIRROR_CMD = "aria"
STATUS_CMD = "status"
INFO_CMD = "info"
LOG_CMD = "log"
QBIT_CMD = "qbit"
NGROK_CMD = "ngrok"
GDRIVE_BASE_URL = "https://drive.google.com/uc?id={}&export=download"
GDRIVE_FOLDER_BASE_URL = "https://drive.google.com/drive/folders/{}"
TRACKER_URLS = [
    "https://cdn.staticaly.com/gh/XIU2/TrackersListCollection/master/all_aria2.txt",
    "https://raw.githubusercontent.com/hezhijie0327/Trackerslist/main/trackerslist_tracker.txt"
]
DHT_FILE_URL = "https://github.com/P3TERX/aria2.conf/raw/master/dht.dat"
DHT6_FILE_URL = "https://github.com/P3TERX/aria2.conf/raw/master/dht6.dat"
ARIA_COMMAND = "aria2c --allow-overwrite=true --auto-file-renaming=true --check-certificate=false --check-integrity=true "\
               "--continue=true --content-disposition-default-utf8=true --daemon=true --disk-cache=40M --enable-rpc=true "\
               "--force-save=true --http-accept-gzip=true --max-connection-per-server=16 --max-concurrent-downloads=10 "\
               "--max-file-not-found=0 --max-tries=20 --min-split-size=10M --optimize-concurrent-downloads=true --reuse-uri=true "\
               "--quiet=true --rpc-max-request-size=1024M --split=10 --summary-interval=0 --seed-time=0 --bt-enable-lpd=true "\
               "--bt-detach-seed-only=true --bt-remove-unselected-file=true --follow-torrent=mem --bt-tracker={} "\
               "--keep-unfinished-download-result=true --save-not-found=true --save-session=/usr/src/app/aria2.session --save-session-interval=60"
MAGNET_REGEX = r"magnet:\?xt=urn:btih:[a-zA-Z0-9]*"
URL_REGEX = r"(?:(?:https?|ftp):\/\/)?[\w/\-?=%.]+\.[\w/\-?=%.]+"
DOWNLOAD_PATH = "/usr/src/app/downloads"
ARIA_OPTS = {'dir': DOWNLOAD_PATH.rstrip("/"), 'max-upload-limit': '3M'}
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

def count_uploaded_files(creds = None, folder_id: str = None, file_name: str = None) -> int:
    count = 0
    try:
        if folder_id is not None:
            logger.info(f"Getting the count of files present in {folder_id}")
            query = f"mimeType != 'application/vnd.google-apps.folder' and trashed=false and parents in '{folder_id}'"
        else:
            logger.info(f"Searching for: {file_name}")
            file_name = file_name.replace("'", "\\'")
            query = f"fullText contains '{file_name}' and trashed=false and parents in '{GDRIVE_FOLDER_ID}'"
        gdrive_service = build('drive', 'v3', credentials=creds if creds is not None else get_oauth_creds(), cache_discovery=False)
        files_list = gdrive_service.files().list(
            supportsAllDrives=True, includeItemsFromAllDrives=True, corpora='allDrives', q=query,
            spaces='drive', fields='files(id, name, size)').execute()["files"]
        gdrive_service.close()
        count = len(files_list)
    except Exception:
        logger.error(f"Failed to get details of {folder_id}")
    return count

def delete_empty_folder(folder_id: str, creds = None) -> None:
    if folder_id and not count_uploaded_files(folder_id=folder_id):
        logger.info(f"Deleting empty folder: {folder_id} in GDrive")
        try:
            gdrive_service = build('drive', 'v3', credentials=creds if creds is not None else get_oauth_creds(), cache_discovery=False)
            gdrive_service.files().delete(fileId=folder_id, supportsAllDrives=True).execute()
            gdrive_service.close()
        except Exception:
            logger.warning(f"Failed to delete folder: {folder_id}")

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
        media_body=MediaFileUpload(filename=file_path, resumable=True, chunksize=50 * 1024 * 1024),
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

def is_archive_file(file_name: str) -> bool:
    if os.path.isfile(f"{DOWNLOAD_PATH}/{file_name}"):
        return magic.from_file(filename=f"{DOWNLOAD_PATH}/{file_name}", mime=True) in ArchiveMimetypes
    else:
        return False

def upload_to_gdrive(gid: str = None, hash: str = None, name: str = None) -> None:
    count = 0
    creds = None
    folder_id = None
    is_dir = False
    file_name = None
    try:
        aria2c.client.save_session()
        if hash is not None:
            if qb_client := get_qbit_client():
                file_name = qb_client.torrents_files(torrent_hash=hash)[0].get('name').split("/")[0]
                qb_client.auth_log_out()
        elif gid is not None:
            down = aria2c.get_download(gid)
            file_name = down.name
            if down.followed_by_ids or down.is_metadata:
                logger.info(f"Skip uploading of: {file_name}")
                return
        elif name is None:
            logger.error(f"Upload event failed, required param missing")
            return
        else:
            file_name = name
        file_path = f"{DOWNLOAD_PATH}/{file_name}"
        if not os.path.exists(file_path):
            logger.error("Upload event failed, could not find file_name")
            return
        else:
            if creds := get_oauth_creds():
                send_status_update(f"🗂️ <b>File: </b><code>{file_name}</code> <b>upload started</b> ⏳")
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
    except qbittorrentapi.exceptions.NotFound404Error:
        logger.error("Failed to get torrent hash info")
    if is_dir is True:
        if count == count_uploaded_files(creds=creds, folder_id=folder_id):
            send_status_update(f"🗂️ <b>Folder: </b><code>{file_name}</code> <b>uploaded</b> ✔️\n🌐 <b>Link: </b>{GDRIVE_FOLDER_BASE_URL.format(folder_id)}")
        else:
            delete_empty_folder(folder_id, creds)
            send_status_update(f"🗂️ <b>Folder: </b><code>{file_name}</code> upload <b>failed</b>❗\n⚠️ <i>Please check the log for more details using</i> <code>/{LOG_CMD}</code>")

def get_user(update: Update) -> Union[str, int]:
    return update.message.from_user.name if update.message.from_user.name is not None else update.message.chat_id

def get_download_info(down: aria2p.Download) -> str:
    info = f"🗂 <b>Name:</b> <code>{down.name}</code>\n🚦 <b>Status:</b> <code>{down.status}</code>\n📀 <b>Size:</b> <code>{down.total_length_string()}</code>\n"\
            f"📥 <b>Downloaded:</b> <code>{down.completed_length_string()} ({down.progress_string()})</code>\n🧩 <b>Peers:</b> <code>{down.connections}</code>\n"\
            f"⚡ <b>Speed:</b> <code>{down.download_speed_string()}</code>\n⏳ <b>ETA:</b> <code>{down.eta_string()}</code>"
    if down.bittorrent is not None:
        info += f"\n🥑 <b>Seeders:</b> <code>{down.num_seeders}</code>"
    info += f"\n⚙️ <b>Engine: </b><code>Aria2</code>\n📚 <b>Total Files:</b> <code>{len(down.files)}</code>\n"
    # if len(down.files) > 1:
    #     for aria_file in down.files:
    #         if aria_file.is_metadata is True:
    #             continue
    #         info += f"📄 {os.path.basename(aria_file.path.name)} <code>[{aria_file.completed_length_string()}/{aria_file.length_string()}]</code>\n"
    return info

def get_qbit_info(hash: str, client: qbittorrentapi.Client = None) -> str:
    info = ''
    for torrent in client.torrents_info(torrent_hashes=[hash]):
        info += f"🗂 <b>Name:</b> <code>{torrent.name}</code>\n🚦 <b>Status:</b> <code>{torrent.state_enum.value}</code>\n📀 <b>Size:</b> <code>{humanize.naturalsize(torrent.total_size)}</code>\n"\
            f"📥 <b>Downloaded:</b> <code>{humanize.naturalsize(torrent.downloaded)} ({round(number=torrent.progress * 100, ndigits=2)}%)</code>\n📦 <b>Remaining: </b><code>{humanize.naturalsize(torrent.amount_left)}</code>\n"\
            f"🧩 <b>Peers:</b> <code>{torrent.num_leechs}</code>\n🥑 <b>Seeders:</b> <code>{torrent.num_seeds}</code>\n⚡ <b>Speed:</b> <code>{humanize.naturalsize(torrent.dlspeed)}/s</code>\n"\
            f"⏳ <b>ETA:</b> <code>{humanize.naturaldelta(torrent.eta)}</code>\n⚙️ <b>Engine: </b><code>Qbittorent</code>"
        try:
            info += f"\n📚 <b>Total Files:</b> <code>{len(client.torrents_files(torrent_hash=hash))}</code>\n"
            # if len(client.torrents_files(torrent_hash=hash)) > 1:
            #     for torrent_file in client.torrents_files(torrent_hash=hash):
            #         info += f"📄 {os.path.basename(torrent_file.get('name'))} <code>[{round(number=torrent_file.get('progress') * 100, ndigits=1)}%|{humanize.naturalsize(torrent_file.get('size'))}]</code>\n"
        except qbittorrentapi.exceptions.NotFound404Error:
            pass
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

def get_ngrok_btn(file_name: str) -> Optional[InlineKeyboardButton]:
    try:
        if tunnels := ngrok.get_tunnels():
            return InlineKeyboardButton(text="🌐 Ngrok URL", url=f"{tunnels[0].public_url}/{quote(file_name)}")
    except (IndexError, ngrok.PyngrokError) as err:
        logger.error(f"Failed to get ngrok tunnel, error: {str(err)}")
        return None

def get_buttons(prog: str, dl_info: str) -> Dict[str, InlineKeyboardButton]:
    return {
        "refresh": InlineKeyboardButton(text="♻ Refresh", callback_data=f"{prog}-refresh#{dl_info}"),
        "delete": InlineKeyboardButton(text="🚫 Delete", callback_data=f"{prog}-remove#{dl_info}"),
        "retry": InlineKeyboardButton(text="🚀 Retry", callback_data=f"{prog}-retry#{dl_info}"),
        "resume": InlineKeyboardButton(text="▶ Resume", callback_data=f"{prog}-resume#{dl_info}"),
        "pause": InlineKeyboardButton(text="⏸ Pause", callback_data=f"{prog}-pause#{dl_info}"),
        "upload": InlineKeyboardButton(text="☁️ Upload", callback_data=f"{prog}-upload#{dl_info}"),
        "extract": InlineKeyboardButton(text="🗃️ Extract", callback_data=f"{prog}-extract#{dl_info}"),
        "show_all": InlineKeyboardButton(text=f"🔆 Show All ({get_downloads_count()})", callback_data=f"{prog}-lists")
    }

def get_aria_keyboard(down: aria2p.Download) -> InlineKeyboardMarkup:
    buttons = get_buttons("aria", down.gid)
    ngrok_btn = get_ngrok_btn(down.name)
    action_btn = [[buttons["show_all"], buttons["delete"]]]
    if "error" == down.status:
        action_btn.insert(0, [buttons["refresh"], buttons["retry"]])
    elif "paused" == down.status:
        action_btn.insert(0, [buttons["refresh"], buttons["resume"]])
    elif "active" == down.status:
        action_btn.insert(0, [buttons["refresh"], buttons["pause"]])
    elif "complete" == down.status and down.is_metadata is False and not down.followed_by_ids:
        if ngrok_btn:
            if is_archive_file(down.name):
                action_btn = [[ngrok_btn, buttons["extract"]], [buttons["upload"], buttons["delete"]], [buttons["show_all"]]]
            else:
                action_btn = [[ngrok_btn, buttons["upload"]], [buttons["show_all"], buttons["delete"]]]
        elif is_archive_file(down.name):
            action_btn = [[buttons["extract"], buttons["upload"]], [buttons["show_all"], buttons["delete"]]]
        else:
            action_btn = [[buttons["upload"], buttons["delete"]], [buttons["show_all"]]]
    else:
        action_btn = [[buttons["refresh"], buttons["delete"]], [buttons["show_all"]]]
    return InlineKeyboardMarkup(action_btn)

def get_qbit_keyboard(qbit: qbittorrentapi.TorrentDictionary = None) -> InlineKeyboardMarkup:
    buttons = get_buttons("qbit", qbit.hash)
    file_name = qbit.files[0].get('name').split("/")[0] if qbit.files else qbit.get('name')
    ngrok_btn = get_ngrok_btn(file_name)
    action_btn = [[buttons["show_all"], buttons["delete"]]]
    if qbit.state_enum.is_errored:
        action_btn.insert(0, [buttons["refresh"], buttons["retry"]])
    elif "pausedDL" == qbit.state_enum.value:
        action_btn.insert(0, [buttons["refresh"], buttons["resume"]])
    elif qbit.state_enum.is_downloading:
        action_btn.insert(0, [buttons["refresh"], buttons["pause"]])
    elif qbit.state_enum.is_complete or "pausedUP" == qbit.state_enum.value:
        if ngrok_btn:
            if is_archive_file(file_name):
                action_btn = [[ngrok_btn, buttons["extract"]], [buttons["upload"], buttons["delete"]], [buttons["show_all"]]]
            else:
                action_btn.insert(0, [ngrok_btn, buttons["upload"]])
        elif is_archive_file(file_name):
            action_btn.insert(0, [buttons["extract"], buttons["upload"]])
        else:
            action_btn = [[buttons["upload"], buttons["delete"]], [buttons["show_all"]]]
    else:
        action_btn = [[buttons["refresh"], buttons["delete"]], [buttons["show_all"]]]
    return InlineKeyboardMarkup(action_btn)

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
    except error.TelegramError as err:
        logger.debug(f"Failed to edit message for: {callback.data} error: {str(err)}")

async def get_total_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

def get_sys_info() -> str:
    avg_cpu_temp = ""
    if temp := psutil.sensors_temperatures():
        if "coretemp" in temp:
            key = "coretemp"
        elif "cpu_thermal" in temp:
            key = "cpu_thermal"
        else:
            key = None
        if key:
            cpu_temp = 0
            for t in temp[key]:
                cpu_temp += t.current
            avg_cpu_temp += f"{round(number=cpu_temp/len(temp[key]), ndigits=2)}°C"
    else:
        avg_cpu_temp += "NA"
    details = f"<b>CPU Usage :</b> {psutil.cpu_percent(interval=None)}%\n" \
              f"<b>CPU Freq  :</b> {math.ceil(psutil.cpu_freq(percpu=False).current)} MHz\n" \
              f"<b>CPU Cores :</b> {psutil.cpu_count(logical=True)}\n" \
              f"<b>CPU Temp  :</b> {avg_cpu_temp}\n" \
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
        details += f"\n<b>Ngrok URL:</b> {ngrok.get_tunnels()[0].public_url}"
    except (OverflowError, IndexError, ngrok.PyngrokError):
        pass
    return details

async def trigger_upload(name: str, prog: str, file_id: str, update: Update, origin: bool = True) -> None:
    if not origin and is_archive_file(name):
        msg = f"🗂️ <b>File:</b> <code>{name}</code> <b>is an archive</b> so do you want to upload as it is or upload the extracted contents❓"
        await edit_message(msg, update.callback_query, InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="📦 Original", callback_data=f"{prog}-upload-orig#{file_id}"),
              InlineKeyboardButton(text="🗃 Extracted", callback_data=f"{prog}-upext#{file_id}")],
             [InlineKeyboardButton(text="⬅️ Back", callback_data=f"{prog}-file#{file_id}")]]
        ))
    elif count_uploaded_files(file_name=name) > 0:
        msg = f"🗂️ <b>File:</b> <code>{name}</code> <b>is already uploaded</b> and can be found in {GDRIVE_FOLDER_BASE_URL.format(GDRIVE_FOLDER_ID)}"
        await edit_message(msg, update.callback_query, InlineKeyboardMarkup([[InlineKeyboardButton(text="⬅️ Back", callback_data=f"{prog}-file#{file_id}")]]))
    else:
        msg = f"🌈 <b>Upload started for: </b><code>{name}</code>\n⚠️ <i>Do not press the upload button again unless the upload has failed, you'll receive status updates on the same</i>"
        threading.Thread(target=upload_to_gdrive, kwargs={'name': name}, daemon=True).start()
        logger.info(f"Upload thread started for: {name}")
        await edit_message(msg, update.callback_query, InlineKeyboardMarkup([[InlineKeyboardButton(text="⬅️ Back", callback_data=f"{prog}-file#{file_id}")]]))

def is_file_extracted(file_name: str) -> bool:
    folder_name = os.path.splitext(file_name)[0]
    folder_path = f"{DOWNLOAD_PATH}/{folder_name}"
    try:
        folder_size = shutil.disk_usage(folder_path).used if os.path.exists(folder_path) else 0
        file_size = os.path.getsize(f"{DOWNLOAD_PATH}/{file_name}")
        return folder_size >= file_size
    except OSError:
        return False

async def start_extraction(name: str, prog: str, file_id: str, update: Update, upload: bool = False) -> None:
    folder_name = os.path.splitext(name)[0]
    if is_file_extracted(name):
        msg = f"🗂️ <b>File:</b> <code>{name}</code> <b>is already extracted</b>"
        if tunnel := ngrok.get_tunnels():
            msg += f"\n🌎 <b>URL:</b> {tunnel[0].public_url}/{quote(folder_name)}"
        await edit_message(msg, update.callback_query, InlineKeyboardMarkup([[InlineKeyboardButton(text="⬅️ Back", callback_data=f"{prog}-file#{file_id}")]]))
    else:
        msg = f"🗃️ <b>Extraction started for: </b><code>{name}</code>\n⚠️ <i>Do not press the extract button again unless it has failed, you'll receive status updates on the same.</i>"
        if upload is True:
            msg += f" <i>Upload process will be started once it completes.</i>"
        await edit_message(msg, update.callback_query, InlineKeyboardMarkup([[InlineKeyboardButton(text="⬅️ Back", callback_data=f"{prog}-file#{file_id}")]]))
        os.makedirs(name=f"{DOWNLOAD_PATH}/{folder_name}", exist_ok=True)
        try:
            patoolib.extract_archive(archive=f"{DOWNLOAD_PATH}/{name}", outdir=f"{DOWNLOAD_PATH}/{folder_name}", interactive=False)
            msg = f"🗂️ <b>File:</b> <code>{name}</code> <b>extracted</b> ✔️"
            if tunnel := ngrok.get_tunnels():
                msg += f"\n🌎 <b>URL:</b> {tunnel[0].public_url}/{quote(folder_name)}"
            send_status_update(msg)
            if upload is True:
                threading.Thread(target=upload_to_gdrive, kwargs={'name': folder_name}, daemon=True).start()
        except patoolib.util.PatoolError as err:
            shutil.rmtree(path=f"{DOWNLOAD_PATH}/{folder_name}", ignore_errors=True)
            send_status_update(f"⁉️ <b>Failed to extract:</b> <code>{name}</code>\n⚠️ <b>Error:</b> <code>{str(err).replace('>', '').replace('<', '')}</code>\n<i>Check /{LOG_CMD} for more details.</i>")

def remove_extracted_dir(file_name: str) -> None:
    if is_archive_file(file_name) and os.path.exists(f"{DOWNLOAD_PATH}/{os.path.splitext(file_name)[0]}"):
        shutil.rmtree(path=f"{DOWNLOAD_PATH}/{os.path.splitext(file_name)[0]}", ignore_errors=True)

async def upext_handler(file_name: str, prog: str, file_id: str, update: Update) -> None:
    if is_file_extracted(file_name):
        await trigger_upload(os.path.splitext(file_name)[0], prog, file_id, update)
    else:
        await start_extraction(file_name, prog, file_id, update, True)

async def bot_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                await edit_message(get_download_info(aria_obj.live), update.callback_query, get_aria_keyboard(aria_obj))
            elif action in ["aria-retry", "aria-remove", "aria-lists"]:
                if "retry" in action:
                    aria2c.retry_downloads(downloads=[aria_obj], clean=False)
                elif "remove" in action:
                    remove_extracted_dir(aria_obj.name)
                    aria2c.remove(downloads=[aria_obj], force=True, files=True, clean=True)
                await get_total_downloads(update, context)
            elif "upload" in action:
                await trigger_upload(aria_obj.name, "aria", callback_data[1], update, True if "orig" in action else False)
            elif "extract" in action:
                await start_extraction(aria_obj.name, "aria", callback_data[1], update)
            elif "upext" in action:
                await upext_handler(aria_obj.name, "aria", callback_data[1], update)
        elif "qbit" in action:
            torrent_hash = callback_data[1].strip() if len(callback_data) > 1 else None
            if qb_client := get_qbit_client():
                if torrent_hash is None:
                    name = None
                elif qb_client.torrents_files(torrent_hash):
                    name = qb_client.torrents_files(torrent_hash)[0].get('name').split("/")[0]
                else:
                    name = qb_client.torrents_info(torrent_hashes=[torrent_hash])[0].get('name')
                if action in ["qbit-refresh", "qbit-file", "qbit-pause", "qbit-resume"]:
                    if "pause" in action:
                        qb_client.torrents_pause(torrent_hashes=[torrent_hash])
                    elif "resume" in action:
                        qb_client.torrents_resume(torrent_hashes=[torrent_hash])
                    if msg := get_qbit_info(torrent_hash, qb_client):
                        await edit_message(msg, update.callback_query, get_qbit_keyboard(qb_client.torrents_info(torrent_hashes=[torrent_hash])[0]))
                    else:
                        await edit_message("<b>Torrent not found</b> ❗", update.callback_query)
                elif action in ["qbit-retry", "qbit-remove", "qbit-lists"]:
                    if "retry" in action:
                        qb_client.torrents_set_force_start(enable=True, torrent_hashes=[torrent_hash])
                    elif "remove" in action:
                        remove_extracted_dir(name)
                        qb_client.torrents_delete(delete_files=True, torrent_hashes=[torrent_hash])
                    await get_total_downloads(update, context)
                elif "upload" in action:
                    await trigger_upload(name, "qbit", torrent_hash, update, True if "orig" in action else False)
                elif "extract" in action:
                    await start_extraction(name, "qbit", torrent_hash, update)
                elif "upext" in action:
                    await upext_handler(name, "qbit", torrent_hash, update)
                qb_client.auth_log_out()
        else:
            if "refresh" == callback_data[1]:
                await edit_message(get_sys_info(), update.callback_query, InlineKeyboardMarkup([[InlineKeyboardButton(text="♻️ Refresh", callback_data="sys#refresh"), InlineKeyboardButton(text="🚫 Close", callback_data="sys#close")]]))
            if "close" == callback_data[1]:
                try:
                    await update.callback_query.delete_message()
                except error.TelegramError:
                    await edit_message("<b>Sys info data cleared</b>", update.callback_query)
    except aria2p.ClientException:
        await edit_message(f"⁉️ <b>Unable to find GID:</b> <code>{update.callback_query.data}</code>", update.callback_query)
    except qbittorrentapi.exceptions.APIError:
        await edit_message(f"⁉️<b>Unable to find Torrent:</b> <code>{update.callback_query.data}</code>", update.callback_query)
    except (error.TelegramError, requests.exceptions.RequestException, IndexError, ValueError, RuntimeError):
        logger.error(f"Failed to answer callback for: {update.callback_query.data}")

async def sys_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_message(get_sys_info(), update, context,
                        InlineKeyboardMarkup([[InlineKeyboardButton(text="♻️ Refresh", callback_data="sys#refresh"),
                                               InlineKeyboardButton(text="🚫 Close", callback_data="sys#close")]]), False)

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

async def ngrok_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Getting ngrok tunnel info")
    try:
        if tunnels := ngrok.get_tunnels():
            await reply_message(f"🌐 <b>Ngrok URL:</b> {tunnels[0].public_url}", update, context)
        else:
            raise IndexError("No tunnel found")
    except (IndexError, ngrok.PyngrokNgrokURLError, ngrok.PyngrokNgrokHTTPError) as err:
        logger.error(f"Failed to get ngrok tunnel, error: {str(err)}")
        logger.info("Restarting ngrok tunnel")
        try:
            if ngrok.process.is_process_running(conf.get_default().ngrok_path) is True:
                ngrok.kill()
                await sleep(1)
            file_tunnel = ngrok.connect(addr=f"file://{DOWNLOAD_PATH}", proto="http", schemes=["http"], name="files_tunnel", inspect=False)
            await reply_message(f"🌍 <b>Ngrok tunnel started\nURL:</b> {file_tunnel.public_url}", update, context)
        except ngrok.PyngrokError as err:
            await reply_message(f"⁉️ <b>Failed to get tunnel info</b>\nError: <code>{str(err)}</code>", update, context)

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
             (NGROK_CMD, "🌍 Show Ngrok URL"),
             (LOG_CMD, "📄 Get runtime log file")]
        )
    except (error.TelegramError, RuntimeError):
        logger.error("Failed to set commands")
    await reply_message(
        f"Hi 👋, Welcome to <b>Mirror2Gdrive</b> bot. I can mirror files to your GDrive. Please use <code>/{MIRROR_CMD}</code> or <code>/{QBIT_CMD}</code> cmd to send links.",
        update, context
    )

async def is_torrent_file(doc: Document, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    logger.info(f"Fetching file: {doc.file_name}")
    tg_file = await context.bot.get_file(file_id=doc.file_id)
    tg_file_path = await tg_file.download_to_drive(custom_path=f"/tmp/{tg_file.file_id}")
    if magic.from_file(tg_file_path, mime=True) == "application/x-bittorrent":
        return tg_file_path
    else:
        return None

async def aria_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/{MIRROR_CMD} sent by {get_user(update)}")
    aria_obj: Optional[aria2p.Download] = None
    link: Optional[str] = None
    try:
        cmd_txt = update.message.text.strip().split(" ", maxsplit=1)
        upload_mode = "M" if len(cmd_txt) > 1 and re.search("^-M", cmd_txt[1], re.IGNORECASE) else "A"
        if reply_msg := update.message.reply_to_message:
            if reply_doc := reply_msg.document:
                if file_path := await is_torrent_file(reply_doc, context):
                    logger.info(f"Adding file to download: {reply_doc.file_name}")
                    aria_obj = aria2c.add_torrent(torrent_file_path=file_path)
                else:
                    await reply_message(f"❗<b>Given file type not supported, please send a torrent file.</b>", update, context)
            elif reply_text := reply_msg.text:
                link = reply_text
            else:
                await reply_message(f"❗<b>Unsupported reply given, please reply with a torrent file or link.</b>", update, context)
        else:
            link = cmd_txt[1][2: len(cmd_txt[1])].strip() if upload_mode == "M" else cmd_txt[1].strip()
        if link is not None:
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
                UPLOAD_MODE_DICT[aria_obj.gid] = upload_mode
                await reply_message(f"📥 <b>Download started</b> ✔️\n<i>Send /{STATUS_CMD} to view</i>", update, context)
            else:
                logger.error(f"Failed to start download, error: {aria_obj.error_code}")
                await reply_message(f"⚠️ <b>Failed to start download</b>\nError:<code>{aria_obj.error_message}</code> ❗", update, context)
        aria2c.client.save_session()
    except IndexError:
        await reply_message(f"😡 <b>Send a link along with the command or reply to it. You can also reply to a .torrent file</b>❗\n<i>Send /{MIRROR_CMD} -m to disable auto upload</i>", update, context)
    except aria2p.ClientException:
        await reply_message("❗ <b>Failed to start download, kindly check the link and retry.</b>", update, context)
    except error.TelegramError:
        await reply_message("❗<b>Failed to process the given torrent file</b>", update, context)

async def qbit_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"/{QBIT_CMD} sent by {get_user(update)}")
    link: Optional[str] = None
    resp: Optional[str] = None
    if qb_client := get_qbit_client():
        try:
            cmd_txt = update.message.text.strip().split(" ", maxsplit=1)
            upload_mode = "M" if len(cmd_txt) > 1 and re.search("^-M", cmd_txt[1], re.IGNORECASE) else "A"
            if reply_msg := update.message.reply_to_message:
                if reply_doc := reply_msg.document:
                    if file_path := await is_torrent_file(reply_doc, context):
                        logger.info(f"Adding file to download: {reply_doc.file_name}")
                        resp = qb_client.torrents_add(torrent_files=file_path)
                    else:
                        await reply_message(f"❗<b>Given file type not supported, please send a torrent file.</b>", update, context)
                        return
                elif reply_text := reply_msg.text:
                    link = reply_text
                else:
                    await reply_message(f"❗<b>Unsupported reply given, please reply with a torrent file or link.</b>", update, context)
                    return
            else:
                link = cmd_txt[1][2: len(cmd_txt[1])].strip() if upload_mode == "M" else cmd_txt[1].strip()
            if link is not None:
                resp = qb_client.torrents_add(urls=link)
            if resp is not None and resp == "Ok.":
                await reply_message(f"🧲 <b>Torrent added</b> ✔️\n<i>Send /{STATUS_CMD} to view</i>", update, context)
                for torrent in qb_client.torrents_info(status_filter='all', sort='added_on', reverse=True, limit=1):
                    UPLOAD_MODE_DICT[torrent.get('hash')] = upload_mode
            else:
                await reply_message("❗ <b>Failed to add it</b>\n⚠️ <i>Kindly verify the given link and retry</i>", update, context)
        except IndexError:
            await reply_message(f"😡 <b>Send a link along with the command or reply to it. You can also reply to a .torrent file</b>❗\n<i>Send /{QBIT_CMD} -m to disable auto upload</i>", update, context)
        except error.TelegramError:
            await reply_message("❗<b>Failed to process the given torrent file</b>", update, context)
        finally:
            qb_client.auth_log_out()
    else:
        await reply_message("⁉️ <b>Error connecting to qbittorrent, please retry</b>", update, context)

def aria_listener(api: aria2p.API = None, gid: str = None) -> None:
    logger.info("aria download complete event trigger")
    try:
        down = aria2c.get_download(gid)
        if not down.is_metadata and not down.followed_by_ids:
            msg = f"✅ <b>Downloaded: </b><code>{down.name}</code>\n📀 <b>Size: </b><code>{humanize.naturalsize(down.total_length)}</code>"
            if tunnel := ngrok.get_tunnels():
                msg += f"\n🌎 <b>URL:</b> {tunnel[0].public_url}/{quote(down.name)}"
            send_status_update(msg)
            if gid in UPLOAD_MODE_DICT and UPLOAD_MODE_DICT.get(gid) == "A":
                upload_to_gdrive(gid)
        else:
            for fgid in down.followed_by_ids:
                UPLOAD_MODE_DICT[fgid] = UPLOAD_MODE_DICT.get(gid) if gid in UPLOAD_MODE_DICT else "M"
            logger.info(f"Removing file: {down.name}")
            aria2c.remove(downloads=[down])
    except aria2p.ClientException as err:
        logger.error(f"Error in aria listener: {str(err)}")

def qbit_listener() -> None:
    logger.info("Starting qbit download event listener")
    while True:
        if qb_client := get_qbit_client():
            for torrent in qb_client.torrents_info(status_filter="all"):
                present_in_dict = torrent.get('hash') in QBIT_STATUS_DICT
                if torrent.state_enum.is_complete or "pausedUP" == torrent.state_enum.value:
                    if present_in_dict:
                        if QBIT_STATUS_DICT.get(torrent.get('hash')) == "NOT_SENT":
                            msg = f"✅ <b>Downloaded: </b><code>{torrent.get('name')}</code>\n📀 <b>Size: </b><code>{humanize.naturalsize(torrent.get('size'))}</code>\n" \
                                  f"⏳ <b>Time taken: </b><code>{humanize.naturaldelta(torrent.get('completion_on') - torrent.get('added_on'))}</code>"
                            if tunnel := ngrok.get_tunnels():
                                file_name = torrent.files[0].get('name').split("/")[0] if torrent.files else torrent.get('name')
                                msg += f"\n🌎 <b>URL:</b> {tunnel[0].public_url}/{quote(file_name)}"
                            send_status_update(msg)
                            QBIT_STATUS_DICT[torrent.get('hash')] = "SENT"
                            if torrent.get('hash') in UPLOAD_MODE_DICT and UPLOAD_MODE_DICT.get(torrent.get('hash')) == "A":
                                upload_to_gdrive(hash=torrent.get('hash'))
                    else:
                        QBIT_STATUS_DICT[torrent.get('hash')] = "NOT_SENT"
                if torrent.state_enum.is_errored:
                    if present_in_dict:
                        if QBIT_STATUS_DICT.get(torrent.get('hash')) == "NOT_SENT":
                            send_status_update(f"❌ <b>Failed: </b><code>{torrent.get('name')}</code>")
                            QBIT_STATUS_DICT[torrent.get('hash')] = "SENT"
                    else:
                        QBIT_STATUS_DICT[torrent.get('hash')] = "NOT_SENT"
            qb_client.auth_log_out()
        time.sleep(10)

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
            status_handler = CommandHandler(STATUS_CMD, get_total_downloads, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            info_handler = CommandHandler(INFO_CMD, sys_info_handler, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            log_handler = CommandHandler(LOG_CMD, send_log_file, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            qbit_handler = CommandHandler(QBIT_CMD, qbit_upload, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            ngrok_handler = CommandHandler(NGROK_CMD, ngrok_info, Chat(chat_id=AUTHORIZED_USERS, allow_empty=False))
            callback_handler = CallbackQueryHandler(bot_callback_handler, pattern="^aria|qbit|sys")
            application.add_handlers([start_handler, aria_handler, callback_handler, status_handler, info_handler, log_handler, qbit_handler, ngrok_handler])
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
    aria_command_args = ARIA_COMMAND.format(trackers).split(' ')
    logger.info("Downloading dht files")
    try:
        dht_file = requests.get(url=DHT_FILE_URL)
        if dht_file.ok:
            with open("/usr/src/app/dht.dat", "wb") as f:
                f.write(dht_file.content)
            aria_command_args.extend(["--enable-dht=true", "--dht-file-path=/usr/src/app/dht.dat"])
        dht6_file = requests.get(url=DHT6_FILE_URL)
        if dht6_file.ok:
            with open("/usr/src/app/dht6.dat", "wb") as f:
                f.write(dht6_file.content)
            aria_command_args.extend(["--enable-dht6=true", "--dht-file-path6=/usr/src/app/dht6.dat"])
    except requests.exceptions.RequestException:
        logger.warning("Failed to download dht file")
    else:
        dht_file.close()
        dht6_file.close()
    if os.path.exists("/usr/src/app/aria2.session"):
        aria_command_args.append("--input-file=/usr/src/app/aria2.session")
    logger.info("Starting aria2c daemon")
    try:
        subprocess.run(args=aria_command_args, check=True)
        time.sleep(2)
        aria2c = aria2p.API(aria2p.Client(host="http://localhost", port=6800, secret=""))
        aria2c.listen_to_notifications(threaded=True, on_download_complete=aria_listener)
        aria2c.get_downloads()
        logger.info("aria2c daemon started")
    except (subprocess.CalledProcessError, aria2p.client.ClientException, requests.exceptions.RequestException, OSError) as err:
        logger.error(f"Failed to start aria2c, error: {str(err)}")
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
        threading.Thread(target=qbit_listener, daemon=False).start()
    except (subprocess.CalledProcessError, AttributeError, qbittorrentapi.exceptions.APIConnectionError,
            qbittorrentapi.exceptions.LoginFailed) as err:
        logger.error(f"Failed to start qbittorrent-nox, error: {str(err)}")
        exit()

def start_ngrok() -> None:
    logger.info("Starting ngrok tunnel")
    with open("/usr/src/app/ngrok.yml", "w") as config:
        config.write(f"version: 2\nauthtoken: {NGROK_AUTH_TOKEN}\nregion: in\nconsole_ui: false\nlog_level: info")
    ngrok_conf = conf.PyngrokConfig(
        config_path="/usr/src/app/ngrok.yml",
        auth_token=NGROK_AUTH_TOKEN,
        region="in",
        max_logs=5,
        ngrok_version="v3",
        monitor_thread=False)
    try:
        conf.set_default(ngrok_conf)
        file_tunnel = ngrok.connect(addr=f"file://{DOWNLOAD_PATH}", proto="http", schemes=["http"], name="files_tunnel", inspect=False)
        logger.info(f"Ngrok tunnel started: {file_tunnel.public_url}")
    except ngrok.PyngrokError as err:
        logger.error(f"Failed to start ngrok, error: {str(err)}")
        exit()

def setup_bot() -> None:
    global BOT_TOKEN
    global NGROK_AUTH_TOKEN
    global GDRIVE_FOLDER_ID
    global AUTHORIZED_USERS
    os.makedirs(name=DOWNLOAD_PATH, exist_ok=True)
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
                    NGROK_AUTH_TOKEN = os.environ['NGROK_AUTH_TOKEN']
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
                                start_ngrok()
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
