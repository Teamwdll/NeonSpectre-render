from logging import getLogger, ERROR
from os import remove as osremove, walk, path as ospath, rename as osrename
from time import time, sleep
from hydrogram.errors import FloodWait, RPCError
from PIL import Image
from threading import RLock
import asyncio
from bot import user_data, GLOBAL_EXTENSION_FILTER, app, tgBotMaxFileSize, premium_session, config_dict
from bot.helper.ext_utils.fs_utils import take_ss, get_media_info, get_media_streams, clean_unwanted
from bot.helper.ext_utils.bot_utils import get_readable_file_size, change_filename, get_bot_pm
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

LOGGER = getLogger(__name__)
getLogger("hydrogram").setLevel(ERROR)
IMAGE_SUFFIXES = ("JPG", "JPX", "PNG", "CR2", "TIF", "BMP", "JXR", "PSD", "ICO", "HEIC", "JPEG")

class TgUploader:

    def __init__(self, name=None, path=None, size=0, listener=None):
        self.name = name
        self.uploaded_bytes = 0
        self._last_uploaded = 0
        self.__listener = listener
        self.__path = path
        self.__start_time = time()
        self.__total_files = 0
        self.__is_cancelled = False
        self.__as_doc = config_dict['AS_DOCUMENT']
        self.__thumb = f"Thumbnails/{listener.message.from_user.id}.jpg"
        self.__msgs_dict = {}
        self.__corrupted = 0
        self.__resource_lock = RLock()
        self.__is_corrupted = False
        self.__sent_msg = None  # Initialize to None, fetch message asynchronously later
        self.__size = size
        self.__user_settings()
        self.__leech_log = user_data.get('is_leech_log')
        self.__app = app
        self.__user_id = listener.message.from_user.id
        self.__button = InlineKeyboardMarkup([[InlineKeyboardButton(text='Save Message', callback_data="save")]]) if config_dict['SAVE_MSG'] else None
        self.isPrivate = listener.message.chat.type in ['private', 'group']

        # Fetch initial message asynchronously if needed
        if self.__listener and self.__listener.message and self.__listener.uid:
            asyncio.ensure_future(self.__fetch_initial_message())

    async def __fetch_initial_message(self):
        try:
            self.__sent_msg = await app.get_messages(self.__listener.message.chat.id, self.__listener.uid)
        except Exception as e:
            LOGGER.error(f"Failed to fetch initial message: {e}")

    def upload(self, o_files):
        for dirpath, subdir, files in sorted(walk(self.__path)):
            for file_ in sorted(files):
                if file_ in o_files:
                    continue
                if not file_.lower().endswith(tuple(GLOBAL_EXTENSION_FILTER)):
                    up_path = ospath.join(dirpath, file_)
                    self.__total_files += 1
                    try:
                        if ospath.getsize(up_path) == 0:
                            LOGGER.error(f"{up_path} size is zero, telegram don't upload zero size files")
                            self.__corrupted += 1
                            continue
                    except Exception as e:
                        if self.__is_cancelled:
                            return
                        LOGGER.error(e)
                        continue
                    self.__upload_file(up_path, file_, dirpath)
                    if self.__is_cancelled:
                        return
                    if not self.__listener.isPrivate and not self.__is_corrupted:
                        self.__msgs_dict[self.__sent_msg.link] = file_
                    self._last_uploaded = 0
                    sleep(1)
        if self.__listener.seed and not self.__listener.newDir:
            clean_unwanted(self.__path)
        if self.__total_files == 0:
            self.__listener.onUploadError('No files to upload. Make sure if you filled USER_SESSION_STRING then you should use supergroup. In case you filled EXTENSION_FILTER then check if all file have this extension')
            return
        if self.__total_files <= self.__corrupted:
            self.__listener.onUploadError('Files Corrupted. Check logs!')
            return
        LOGGER.info(f"Leech Completed: {self.name}")
        size = get_readable_file_size(self.__size)
        self.__listener.onUploadComplete(None, size, self.__msgs_dict, self.__total_files, self.__corrupted, self.name)

    async def __upload_file(self, up_path, file_, dirpath):
        fsize = ospath.getsize(up_path)
        user_id_ = self.__listener.message.from_user.id

        client = premium_session if fsize > 2097152000 else app
        up_path, file_, cap_mono = change_filename(file_, user_id_, dirpath, up_path)

        dumpid = user_data[user_id_].get('userlog') if user_id_ in user_data and user_data[user_id_].get('userlog') else ''
        LEECH_X = int(dumpid) if len(dumpid) != 0 else user_data.get('is_log_leech', [''])[0]

        BOT_PM_X = get_bot_pm(user_id_)

        notMedia = False
        thumb = self.__thumb
        self.__is_corrupted = False
        try:
            is_video, is_audio = get_media_streams(up_path)
            if not self.__as_doc:
                if is_video:
                    duration = get_media_info(up_path)[0]
                    if thumb is None:
                        thumb = take_ss(up_path, duration)
                        if self.__is_cancelled:
                            if self.__thumb is None and thumb is not None and ospath.lexists(thumb):
                                osremove(thumb)
                            return
                    if thumb is not None:
                        with Image.open(thumb) as img:
                            width, height = img.size
                    else:
                        width = 480
                        height = 320
                    if not file_.upper().endswith(("MKV", "MP4")):
                        file_ = f"{ospath.splitext(file_)[0]}.mp4"
                        new_path = ospath.join(dirpath, file_)
                        osrename(up_path, new_path)
                        up_path = new_path
                    if 'is_leech_log' in user_data and user_data.get('is_leech_log'):
                        for leechchat in self.__leech_log:
                            if ospath.getsize(up_path) > tgBotMaxFileSize:
                                usingclient = premium_session
                            else:
                                usingclient = self.__app
                            self.__sent_msg = await usingclient.send_video(
                                chat_id=int(leechchat),
                                video=up_path,
                                caption=cap_mono,
                                duration=duration,
                                width=width,
                                height=height,
                                thumb=thumb,
                                supports_streaming=True,
                                disable_notification=True,
                                reply_markup=self.__button,
                                progress=self.__upload_progress
                            )
                            if BOT_PM_X:
                                try:
                                    await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                                except Exception as err:
                                    LOGGER.error(f"Failed To Send Video in PM:\n{err}")
                            if len(dumpid) != 0:
                                try:
                                    await app.copy_message(chat_id=LEECH_X, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                                except Exception as err:
                                    LOGGER.error(f"Failed To Send Video in dump:\n{err}")

                    else:
                        self.__sent_msg = await self.__app.send_document(
                            chat_id=int(leechchat),
                            document=up_path,
                            thumb=thumb,
                            caption=cap_mono,
                            disable_notification=True,
                            reply_markup=self.__button,
                            progress=self.__upload_progress
                        )
                        if not self.isPrivate and BOT_PM_X:
                            try:
                                await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                            except Exception as err:
                                LOGGER.error(f"Failed To Send Document in PM:\n{err}")

                elif is_audio:
                    duration, artist, title = get_media_info(up_path)
                    if 'is_leech_log' in user_data and user_data.get('is_leech_log'):
                        for leechchat in self.__leech_log:
                            if ospath.getsize(up_path) > tgBotMaxFileSize:
                                usingclient = premium_session
                            else:
                                usingclient = self.__app
                            self.__sent_msg = await usingclient.send_audio(
                                chat_id=int(leechchat),
                                audio=up_path,
                                caption=cap_mono,
                                duration=duration,
                                performer=artist,
                                title=title,
                                thumb=thumb,
                                disable_notification=True,
                                reply_markup=self.__button,
                                progress=self.__upload_progress
                            )
                            if BOT_PM_X:
                                try:
                                    await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                                except Exception as err:
                                    LOGGER.error(f"Failed To Send Audio in PM:\n{err}")
                            if len(dumpid) != 0:
                                try:
                                    await app.copy_message(chat_id=LEECH_X, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                                except Exception as err:
                                    LOGGER.error(f"Failed To Send Audio in dump:\n{err}")
                    else:
                        self.__sent_msg = await self.__sent_msg.reply_audio(
                            audio=up_path,
                            caption=cap_mono,
                            duration=duration,
                            performer=artist,
                            title=title,
                            thumb=thumb,
                            disable_notification=True,
                            reply_markup=self.__button,
                            progress=self.__upload_progress
                        )
                        if not self.isPrivate and BOT_PM_X:
                            try:
                                await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                            except Exception as err:
                                LOGGER.error(f"Failed To Send Audio in PM:\n{err}")
                elif up_path.upper().endswith(IMAGE_SUFFIXES):
                    with Image.open(up_path) as img:
                        width, height = img.size
                    if 'is_leech_log' in user_data and user_data.get('is_leech_log'):
                        for leechchat in self.__leech_log:
                            if ospath.getsize(up_path) > tgBotMaxFileSize:
                                usingclient = premium_session
                            else:
                                usingclient = self.__app
                            self.__sent_msg = await usingclient.send_photo(
                                chat_id=int(leechchat),
                                photo=up_path,
                                caption=cap_mono,
                                width=width,
                                height=height,
                                disable_notification=True,
                                reply_markup=self.__button,
                                progress=self.__upload_progress
                            )
                            if BOT_PM_X:
                                try:
                                    await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                                except Exception as err:
                                    LOGGER.error(f"Failed To Send Photo in PM:\n{err}")
                            if len(dumpid) != 0:
                                try:
                                    await app.copy_message(chat_id=LEECH_X, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                                except Exception as err:
                                    LOGGER.error(f"Failed To Send Photo in dump:\n{err}")
                    else:
                        self.__sent_msg = await self.__sent_msg.reply_photo(
                            photo=up_path,
                            caption=cap_mono,
                            width=width,
                            height=height,
                            disable_notification=True,
                            reply_markup=self.__button,
                            progress=self.__upload_progress
                        )
                        if not self.isPrivate and BOT_PM_X:
                            try:
                                await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                            except Exception as err:
                                LOGGER.error(f"Failed To Send Photo in PM:\n{err}")
                else:
                    notMedia = True
        except Exception as err:
            LOGGER.error(err)
            self.__is_corrupted = True

        if notMedia or self.__as_doc or self.__is_corrupted:
            try:
                if 'is_leech_log' in user_data and user_data.get('is_leech_log'):
                    for leechchat in self.__leech_log:
                        if ospath.getsize(up_path) > tgBotMaxFileSize:
                            usingclient = premium_session
                        else:
                            usingclient = self.__app
                        self.__sent_msg = await usingclient.send_document(
                            chat_id=int(leechchat),
                            document=up_path,
                            caption=cap_mono,
                            thumb=thumb,
                            disable_notification=True,
                            reply_markup=self.__button,
                            progress=self.__upload_progress
                        )
                        if BOT_PM_X:
                            try:
                                await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                            except Exception as err:
                                LOGGER.error(f"Failed To Send Document in PM:\n{err}")
                        if len(dumpid) != 0:
                            try:
                                await app.copy_message(chat_id=LEECH_X, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                            except Exception as err:
                                LOGGER.error(f"Failed To Send Document in dump:\n{err}")

                else:
                    self.__sent_msg = await self.__sent_msg.reply_document(
                        document=up_path,
                        caption=cap_mono,
                        thumb=thumb,
                        disable_notification=True,
                        reply_markup=self.__button,
                        progress=self.__upload_progress
                    )
                    if not self.isPrivate and BOT_PM_X:
                        try:
                            await app.copy_message(chat_id=self.__user_id, from_chat_id=self.__sent_msg.chat.id, message_id=self.__sent_msg.id)
                        except Exception as err:
                            LOGGER.error(f"Failed To Send Document in PM:\n{err}")
            except FloodWait as f:
                LOGGER.warning(f"FloodWait: Sleeping for {f.value}")
                sleep(f.value + 10)
                self.__corrupted += 1
            except RPCError as e:
                LOGGER.error(e)
                self.__is_corrupted = True
            except Exception as err:
                LOGGER.error(err)
                self.__is_corrupted = True
            if not self.__is_corrupted:
                self.__corrupted += 1
        if not self.__thumb is None and thumb is not None and ospath.lexists(thumb):
            osremove(thumb)

    def __upload_progress(self, current, total):
        if self.__is_cancelled:
            self.__app.stop_transmission()
        chunk_size = current - self.uploaded_bytes
        self.__listener.onUploadProgress(self.name, self.__size, self.uploaded_bytes, current, total, time() - self.__start_time, chunk_size)
        self.uploaded_bytes = current

    def cancel_upload(self):
        self.__is_cancelled = True

    def __user_settings(self):
        if self.__listener.user_id in user_data:
            self.__as_doc = user_data[self.__listener.user_id].get('as_doc', self.__as_doc)

    @property
    def is_cancelled(self):
        with self.__resource_lock:
            return self.__is_cancelled
