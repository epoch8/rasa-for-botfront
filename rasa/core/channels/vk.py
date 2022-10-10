import aiohttp
import logging
from copy import deepcopy
from sanic import Blueprint, response
from sanic.request import Request
from sanic.response import HTTPResponse
from typing import Dict, Text, Any, List, Optional, Callable, Awaitable

from rasa.core.channels.channel import InputChannel, UserMessage, OutputChannel
from rasa.shared.constants import INTENT_MESSAGE_PREFIX
from rasa.shared.core.constants import USER_INTENT_RESTART

logger = logging.getLogger(__name__)


class VkOutput(OutputChannel):
    """Output channel for Vk."""

    # skipcq: PYL-W0236
    @classmethod
    def name(cls) -> Text:
        return "vk"

    def __init__(self, access_token: Optional[Text]) -> None:
        self.access_token = access_token
        self.api_version = 5.131

    async def send_message(self, user_id: int, message: Text, keyboard: Optional[dict]):
        data = {
            "user_id": user_id,
            "random": 0,  # TODO
            "peer_id": user_id,
            "message": message,
            "access_token": self.access_token,
            "v": self.api_version
        }
        if keyboard:
            data["keyboard"] = keyboard
        async with aiohttp.ClientSession() as session:
            send_message_url = "https://api.vk.com/method/messages.send"
            async with session.post(send_message_url) as resp:
                data = await resp.json()
                if "error" not in data:
                    return True
                logger.error(f"Sending message ended up with error: {data['error']}")
                return
                

    async def send_text_message(
        self, user_id: Text, message: Text, **kwargs: Any
    ) -> None:
        for message_part in message.strip().split("\n\n"):
            self.send_message(user_id, message_part)

    # TODO send_image_url

    def _create_keyboard_from_buttons(buttons: List[Dict[Text, Any]]):
        def _chunkIt(seq, num):
            out = []
            last = 0
            while last < len(seq):
                out.append(seq[last:(last + num)])
                last += num
            return out
        data = _chunkIt(buttons, 3)
        result = []
        for row in data:
            for i, piece in enumerate(row):
                row[i] = {  
                    "action": {  
                        "type": piece["title"],
                        "payload": piece["payload"],
                        "label":"Red"
                    }
                }

    async def send_text_with_buttons(
        self,
        user_id: Text,
        message: Text,
        buttons: List[Dict[Text, Any]],
        button_type: Optional[Text] = "inline",
        **kwargs: Any,
    ) -> None:
        """Sends a message with keyboard.

        For more information: https://dev.vk.com/api/bots/development/keyboard

        :button_type inline: inline keyboard

        :button_type reply: reply keyboard
        """
        data = {
            "one_time": False,
            "buttons": []            
        }
        if button_type == "inline":
            reply_markup = InlineKeyboardMarkup()
            button_list = [
                InlineKeyboardButton(s["title"], callback_data=s["payload"])
                for s in buttons
            ]
            reply_markup.row(*button_list)

        elif button_type == "reply":
            reply_markup = ReplyKeyboardMarkup(
                resize_keyboard=False, one_time_keyboard=True
            )
            # drop button_type from button_list
            button_list = [b for b in buttons if b.get("title")]
            for idx, button in enumerate(buttons):
                if isinstance(button, list):
                    reply_markup.add(KeyboardButton(s["title"]) for s in button)
                else:
                    reply_markup.add(KeyboardButton(button["title"]))
        else:
            logger.error(
                "Trying to send text with buttons for unknown "
                "button type {}".format(button_type)
            )
            return

        self.send_message(user_id, message, reply_markup=reply_markup)

    async def send_custom_json(
        self, recipient_id: Text, json_message: Dict[Text, Any], **kwargs: Any
    ) -> None:
        json_message = deepcopy(json_message)

        recipient_id = json_message.pop("chat_id", recipient_id)

        send_functions = {
            ("text",): "send_message",
            ("photo",): "send_photo",
            ("audio",): "send_audio",
            ("document",): "send_document",
            ("sticker",): "send_sticker",
            ("video",): "send_video",
            ("video_note",): "send_video_note",
            ("animation",): "send_animation",
            ("voice",): "send_voice",
            ("media",): "send_media_group",
            ("latitude", "longitude", "title", "address"): "send_venue",
            ("latitude", "longitude"): "send_location",
            ("phone_number", "first_name"): "send_contact",
            ("game_short_name",): "send_game",
            ("action",): "send_chat_action",
            (
                "title",
                "decription",
                "payload",
                "provider_token",
                "start_parameter",
                "currency",
                "prices",
            ): "send_invoice",
        }

        for params in send_functions.keys():
            if all(json_message.get(p) is not None for p in params):
                args = [json_message.pop(p) for p in params]
                api_call = getattr(self, send_functions[params])
                api_call(recipient_id, *args, **json_message)


class TelegramInput(InputChannel):
    """Telegram input channel"""

    @classmethod
    def name(cls) -> Text:
        return "telegram"

    @classmethod
    def from_credentials(cls, credentials: Optional[Dict[Text, Any]]) -> InputChannel:
        if not credentials:
            cls.raise_missing_credentials_exception()

        return cls(
            credentials.get("access_token"),
            credentials.get("verify"),
            credentials.get("webhook_url"),
        )

    def __init__(
        self,
        access_token: Optional[Text],
        verify: Optional[Text],
        webhook_url: Optional[Text],
        debug_mode: bool = True,
    ) -> None:
        self.access_token = access_token
        self.verify = verify
        self.webhook_url = webhook_url
        self.debug_mode = debug_mode

    @staticmethod
    def _is_location(message) -> bool:
        return message.location is not None

    @staticmethod
    def _is_user_message(message) -> bool:
        return message.text is not None

    @staticmethod
    def _is_button(message) -> bool:
        return message.callback_query is not None

    def blueprint(
        self, on_new_message: Callable[[UserMessage], Awaitable[Any]]
    ) -> Blueprint:
        telegram_webhook = Blueprint("telegram_webhook", __name__)
        out_channel = self.get_output_channel()

        @telegram_webhook.route("/", methods=["GET"])
        async def health(_: Request) -> HTTPResponse:
            return response.json({"status": "ok"})

        @telegram_webhook.route("/set_webhook", methods=["GET", "POST"])
        async def set_webhook(_: Request) -> HTTPResponse:
            s = out_channel.setWebhook(self.webhook_url)
            if s:
                logger.info("Webhook Setup Successful")
                return response.text("Webhook setup successful")
            else:
                logger.warning("Webhook Setup Failed")
                return response.text("Invalid webhook")

        @telegram_webhook.route("/webhook", methods=["GET", "POST"])
        async def message(request: Request) -> Any:
            if request.method == "POST":

                request_dict = request.json
                update = Update.de_json(request_dict)
                if not out_channel.get_me().username == self.verify:
                    logger.debug("Invalid access token, check it matches Telegram")
                    return response.text("failed")

                if self._is_button(update):
                    msg = update.callback_query.message
                    text = update.callback_query.data
                else:
                    msg = update.message
                    if self._is_user_message(msg):
                        text = msg.text.replace("/bot", "")
                    elif self._is_location(msg):
                        text = '{{"lng":{0}, "lat":{1}}}'.format(
                            msg.location.longitude, msg.location.latitude
                        )
                    else:
                        return response.text("success")
                sender_id = msg.chat.id
                metadata = self.get_metadata(request)
                try:
                    if text == (INTENT_MESSAGE_PREFIX + USER_INTENT_RESTART):
                        await on_new_message(
                            UserMessage(
                                text,
                                out_channel,
                                sender_id,
                                input_channel=self.name(),
                                metadata=metadata,
                            )
                        )
                        await on_new_message(
                            UserMessage(
                                "/start",
                                out_channel,
                                sender_id,
                                input_channel=self.name(),
                                metadata=metadata,
                            )
                        )
                    else:
                        await on_new_message(
                            UserMessage(
                                text,
                                out_channel,
                                sender_id,
                                input_channel=self.name(),
                                metadata=metadata,
                            )
                        )
                except Exception as e:
                    logger.error(f"Exception when trying to handle message.{e}")
                    logger.debug(e, exc_info=True)
                    if self.debug_mode:
                        raise
                    pass

                return response.text("success")

        return telegram_webhook

    def get_output_channel(self) -> TelegramOutput:
        """Loads the telegram channel."""
        channel = TelegramOutput(self.access_token)
        channel.set_webhook(url=self.webhook_url)

        return channel
