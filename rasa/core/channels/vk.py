import aiohttp
import logging
from sanic import Blueprint, response
from sanic.request import Request
from sanic.response import HTTPResponse, text
from typing import Dict, Text, Any, List, Optional, Callable, Awaitable

from rasa.core.channels.channel import InputChannel, UserMessage, OutputChannel
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
                if "error" in data:
                    logger.error(f"Sending message ended up with error: {data['error']}")
                return
                

    async def send_text_message(
        self, user_id: Text, message: Text, **kwargs: Any
    ) -> None:
        for message_part in message.strip().split("\n\n"):
            self.send_message(user_id, message_part)

    # TODO send_image_url

    @staticmethod
    def form_keyboard_buttons(buttons: List[Dict[Text, Any]]):
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
                        "type": "text",
                        "payload": piece["payload"],
                        "label": piece["title"]
                    }
                }
            result.append(row)
        return result
        

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
        keyboard = {
            "one_time": False,
            "buttons": self.form_keyboard_buttons(buttons)
        }
        if button_type == "inline":
            keyboard["inline"] = True
        elif not button_type == "reply":
            logger.error(
                "Trying to send text with buttons for unknown "
                "button type {}".format(button_type)
            )
            return
        self.send_message(user_id, message, keyboard)


class VkInput(InputChannel):
    """Vk input channel"""

    @classmethod
    def name(cls) -> Text:
        return "vk"

    @classmethod
    def from_credentials(cls, credentials: Optional[Dict[Text, Any]]) -> InputChannel:
        if not credentials:
            cls.raise_missing_credentials_exception()

        return cls(
            credentials.get("access_token"),
            credentials.get("verify"),
            credentials.get("secret_key")
        )

    def __init__(
        self,
        access_token: Optional[Text],
        verify: Optional[Text],
        secret_key: Optional[Text],
        debug_mode: bool = True,
    ) -> None:
        self.access_token = access_token
        self.verify = verify
        self.secret_key = secret_key
        self.debug_mode = debug_mode

    def blueprint(
        self, on_new_message: Callable[[UserMessage], Awaitable[Any]]
    ) -> Blueprint:
        vk_webhook = Blueprint("vk_webhook", __name__)
        out_channel = self.get_output_channel()

        @vk_webhook.route("/", methods=["POST"])
        async def webhook(request: Request) -> HTTPResponse:
            data = request.json
            if data["type"] == "confirmation":
                return text(self.verify)
            if "secret" not in data:
                logger.error("A secret key is not set for the vk group")
                return
            if data["secret"] != self.secret_key:
                logger.error("The secret key doesn't match the passed one")
                return text("Incorrect secret key")

            try:
                if data["type"] == "message_new":
                    message = data["object"]["message"]
                    sender_id = data["from_id"]
                    payload = message["text"]
                    if "payload" in message:
                        payload = dict(message["payload"])
                    await on_new_message(
                        UserMessage(
                            payload,
                            out_channel,
                            sender_id,
                            input_channel=self.name(),
                        )
                    )
            except Exception as e:
                logger.error(f"Exception when trying to handle message.{e}")
                logger.debug(e, exc_info=True)
                if self.debug_mode:
                    raise

            return response.text("success")

        return vk_webhook

    def get_output_channel(self) -> VkOutput:
        """Loads the vk channel."""
        return VkOutput(self.access_token)
