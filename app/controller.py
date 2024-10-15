import re
from io import BytesIO

import telegramify_markdown
from asgiref.sync import sync_to_async
from loguru import logger
from novelai_python.sdk.ai._enum import PROMOTION
from novelai_python.tool.image_metadata import ImageMetadata
from novelai_python.tool.random_prompt import RandomPromptGenerator
from PIL import Image
from telebot import formatting, types, util
from telebot.async_telebot import AsyncTeleBot
from telebot.asyncio_helper import ApiTelegramException
from telebot.asyncio_storage import StateMemoryStorage

from app.event import pipeline_tag
from app_conf import settings
from setting.telegrambot import BotSetting

StepCache = StateMemoryStorage()


def extract_between_multiple_markers(input_list, start_markers, end_markers):
    extracting = False
    extracted_elements = []
    for item in input_list:
        if any(start_marker in item for start_marker in start_markers):
            extracting = True
        if end_markers:
            if any(end_marker in item for end_marker in end_markers):
                break
        if extracting:
            extracted_elements.append(item)
    return extracted_elements


async def read_a111(file: BytesIO):
    message = []
    tipo_model = "Unknown TIPO Model"  # Initialize the TIPO model by default
    tipo_prompt = "Unknown TIPO Prompt"  # Initialize TIPO Prompt by default
    tipo_nlprompt = "Empty"  # Initialize TIPO NL Prompt by default
    tipo_format = "Unknown TIPO Format"  # Initialize TIPO Format by default
    tipo_ban = "Empty"  # Initialize TIPO Ban Tags by default
    tipo_temp = "Unknown TIPO Temperature"  # Initialize TIPO Temperature by default
    tipo_top_p = (
        "Unknown TIPO top_p Temperature"  # Initialize TIPO Top_P Temperature by default
    )
    tipo_top_k = (
        "Unknown TIPO top_k Temperature"  # Initialize TIPO Top_k Temperature by default
    )

    try:
        file.seek(0)
        with Image.open(file) as img:
            parameter = img.info.get("parameters", None)
            if not parameter:
                raise Exception("Empty Parameter")
            if not isinstance(parameter, str):
                parameter = str(parameter)

            # Extracting Prompt (total text to Negative Prompt or to Steps, if there is no Negative Prompt)
            prompt_end = parameter.find("Negative prompt:")
            steps_start = parameter.find("Steps:")

            if prompt_end != -1:
                prompt = parameter[:prompt_end].strip()
            elif steps_start != -1:
                prompt = parameter[:steps_start].strip()
            else:
                prompt = (
                    "❌ Prompt was not found or an error occurred when receiving it."
                )

            # Extraction of Negative Prompt (to Steps)
            negative_start = parameter.find("Negative prompt:")
            if negative_start != -1 and steps_start != -1:
                negative_prompt = parameter[
                    negative_start + len("Negative prompt:") : steps_start
                ].strip()
            else:
                negative_prompt = "❌ Negative Prompt was not found or an error occurred when receiving it."

            # Extracting other information after Steps
            # info = parameter[steps_start:].strip() if steps_start != -1 else "No further info"

            # Looking for Model, Sampler, and CFG Scale
            model = next(
                (
                    p.split(": ")[1]
                    for p in parameter.split(", ")
                    if p.startswith("Model:")
                ),
                "Unknown Model",
            )
            sampler = next(
                (
                    p.split(": ")[1]
                    for p in parameter.split(", ")
                    if p.startswith("Sampler:")
                ),
                "Unknown Sampler",
            )
            cfg_scale = next(
                (
                    p.split(": ")[1]
                    for p in parameter.split(", ")
                    if p.startswith("CFG scale:")
                ),
                "Unknown CFG Scale",
            )
            schedule = next(
                (
                    p.split(": ")[1]
                    for p in parameter.split(", ")
                    if p.startswith("Schedule type:")
                ),
                "Unknown Schedule type",
            )

            # Extracting TIPO parameters manually through searching for curly brackets
            tipo_start = parameter.find('TIPO Parameters: "{')
            tipo_data = None
            if tipo_start != -1:
                tipo_end = parameter.find(
                    '}"', tipo_start
                )  # Find the end of the line with parameters
                if tipo_end != -1:
                    tipo_data = parameter[
                        tipo_start + len('TIPO Parameters: "') : tipo_end + 1
                    ].strip()

                    # Finding a TIPO model inside the parameters
                    tipo_model_match = re.search(r"'model': '([^']+)'", tipo_data)
                    if tipo_model_match:
                        tipo_model = tipo_model_match.group(1)

                    # Search Ban_tags TIPO inside the parameters
                    tipo_ban_match = re.search(r"'ban_tags': '([^']+)'", tipo_data)
                    if tipo_ban_match:
                        tipo_ban = tipo_ban_match.group(1)

                    # Search TIPO Prompt outside of curly brackets
                    tipo_prompt_match = re.search(r'TIPO prompt: "([^"]+)"', parameter)
                    if tipo_prompt_match:
                        tipo_prompt = tipo_prompt_match.group(1).strip()

                    # Search TIPO NL Prompt outside of curly brackets
                    tipo_nlprompt_match = re.search(
                        r"TIPO nl prompt: ([^,]+)", parameter
                    )
                    if tipo_nlprompt_match:
                        tipo_nlprompt = tipo_nlprompt_match.group(1).strip()

                    # Search TIPO Format outside of figure brackets, first try with quotes
                    tipo_format_match = re.search(r'TIPO format: "([^"]+)"', parameter)
                    if tipo_format_match:
                        tipo_format = tipo_format_match.group(1).strip()
                    else:  # If not found with quotation marks, looking for without quotes to comma or end of the line
                        tipo_format_match = re.search(
                            r"TIPO format: ([^,]+)(,|$)", parameter
                        )
                        if tipo_format_match:
                            tipo_format = tipo_format_match.group(1).strip()

                    # Search for TIPO Temperature, top_p, and top_k inside the parameters
                    tipo_temp_match = re.search(r"'temperature': ([^,]+)", tipo_data)
                    if tipo_temp_match:
                        tipo_temp = tipo_temp_match.group(1)
                    tipo_top_p_match = re.search(r"'top_p': ([^,]+)", tipo_data)
                    if tipo_top_p_match:
                        tipo_top_p = tipo_top_p_match.group(1)
                    tipo_top_k_match = re.search(r"'top_k': ([^,]+)", tipo_data)
                    if tipo_top_k_match:
                        tipo_top_k = tipo_top_k_match.group(1)

            if prompt:
                message.append(f"**💡 Original Prompt:** ```{prompt}```")
            if negative_prompt:
                message.append(f"**💢 Negative Prompt:** ```{negative_prompt}```")
            if model:
                message.append(f"**📦 Model:** `{model}`")
            if sampler:
                message.append(
                    f"**📦 Sampler:** `{sampler}`  **Schedule:** `{schedule}`"
                )
            if cfg_scale:
                message.append(f"**📦 CFG Scale:** `{cfg_scale}`")

            # Checking for the use of TIPO and information output
            if tipo_data or tipo_prompt != "Unknown TIPO Prompt":
                message.append(f"**🔖 TIPO is used: `True`**")
                message.append(f"**✏️ TIPO information:**")
                message.append(f"> **✏️ Model:** `{tipo_model}`")
                message.append(f"> **✏️ Prompt:** `{tipo_prompt}`")
                message.append(f"> **✏️ nl Prompt:** `{tipo_nlprompt}`")
            if (
                tipo_format != "Unknown TIPO Format"
            ):  # Display Tipo Format only if it is found
                message.append(f"> **✏️ Format:** `{tipo_format}`")
                message.append(f"> **✏️ Ban Tags:** `{tipo_ban}`")
                message.append(
                    f"> **✏️ Temperature: `{tipo_temp}`  top_p: `{tipo_top_p}`  top_k: `{tipo_top_k}`**"
                )
            else:
                message.append(f"**✏️ TIPO is used: `False`**")

            # if info:
            # message.append(f"\n\n> file info\n{info}")
            # Remove excess empty lines
            message = "\n".join(message).replace("\n\n", "\n")

    except Exception as e:
        logger.debug(f"Error {e}")
        return []

    return [message]


async def read_comfyui(file: BytesIO):
    try:
        file.seek(0)
        with Image.open(file) as img:
            # print(img.info)
            parameter = img.info.get("prompt", None)
            if not parameter:
                raise Exception("Empty Parameter")
    except Exception as e:
        logger.debug(f"Error {e}")
        return []
    else:
        return [f"**📦 Comfyui** \n```{parameter}```"]


async def read_novelai(file: BytesIO, result=None):
    message = []
    try:
        file.seek(0)
        metadata = ImageMetadata.load_image(file)
        read_prompt = metadata.Description if metadata.Description else ""
        read_source = metadata.Source if metadata.Source else ""
        # signed_hash = metadata.Comment.signed_hash if metadata.Comment else ""
        # If Source is empty, try to extract it from text metadata
        if not read_source:
            file.seek(0)
            with Image.open(file) as img:
                read_source = img.info.get("Source", "")

        read_model = PROMOTION.get(read_source, None)
        read_model_value = read_model.value if read_model else ""

        if metadata.Comment:
            sampler = metadata.Comment.sampler if metadata.Comment.sampler else ""
            sm = str(metadata.Comment.sm) if metadata.Comment.sm is not None else ""
            sm_dyn = (
                str(metadata.Comment.sm_dyn)
                if metadata.Comment.sm_dyn is not None
                else ""
            )
            scale = (
                str(metadata.Comment.scale)
                if metadata.Comment.scale is not None
                else ""
            )
            cfg_rescale = (
                str(metadata.Comment.cfg_rescale)
                if metadata.Comment.cfg_rescale is not None
                else ""
            )
            rq_type = (
                metadata.Comment.request_type if metadata.Comment.request_type else ""
            )

            mode = ""
            if rq_type == "PromptGenerateRequest":
                mode += "Text2Image"
            elif rq_type == "Img2ImgRequest":
                mode += "Img2Img"
            elif rq_type == "NativeInfillingRequest":
                mode = "Inpainting"
            if (
                metadata.Comment.reference_strength_multiple
                or metadata.Comment.reference_information_extracted_multiple
            ):
                mode += "+VibeTransfer"

        if read_prompt:
            message.append(f"**💡 Original NovelAI Prompt:** ```{read_prompt}```")
        if read_model_value:
            message.append(f"**📦 Model:** `{read_model_value}`")
        if sampler:
            message.append(f"**📦 Sampler:** `{sampler}`")
            message.append(f"**📦 SMEA:** `{sm}`  DYN: `{sm_dyn}`")
        if scale or cfg_rescale:
            message.append(
                f"**📦 Guidance Scale:** `{scale}`  Rescale: `{cfg_rescale}`"
            )
        if read_source:
            message.append(f"**📦 Source:** `{read_source}`")
        if mode:
            message.append(f"**✏️ Mode:** **`{mode}`**")
        else:
            logger.debug(f"❌ No metadata or error: {metadata.get('error', '')}")
            message.append(
                "❌ An error occurred while processing the request. "
                + metadata.get("error", "")
            )

        # if signed_hash:
        #    message.append("**🧊 Signed by NovelAI**")
        # else:
        #    message.append("**🧊 Not Signed by NovelAI**")

    except Exception as e:
        logger.debug(f"Empty metadata {e}")
        return []

    return message


@sync_to_async
def sync_to_async_func():
    pass


class BotRunner(object):
    def __init__(self):
        self.bot = AsyncTeleBot(BotSetting.token, state_storage=StepCache)

    async def download(self, file):
        assert hasattr(file, "file_id"), "file_id not found"
        name = file.file_id
        _file_info = await self.bot.get_file(file.file_id)
        if isinstance(file, types.PhotoSize):
            name = f"{_file_info.file_unique_id}.jpg"
        if isinstance(file, types.Document):
            name = f"{file.file_unique_id} {file.file_name}"
        if not name.endswith(("jpg", "png", "webp")):
            return None
        downloaded_file = await self.bot.download_file(_file_info.file_path)
        return downloaded_file

    async def tagger(self, file, hidden_long_text=False) -> str:
        raw_file_data = await self.download(file=file)
        if raw_file_data is None:
            return "❌ Does not contain the necessary metadata"
        if isinstance(raw_file_data, bytes):
            file_data = BytesIO(raw_file_data)
        else:
            file_data = raw_file_data
        result = await pipeline_tag(trace_id="test", content=file_data)
        novelai_message = await read_novelai(file=file_data)
        comfyui_message = await read_comfyui(file=file_data)
        a111_message = await read_a111(file=file_data)
        read_message = next(
            filter(lambda msg: msg, [novelai_message]),
            None,
        )
        if read_message and hidden_long_text:
            infer_message = [f"\n>{result.anime_tags}\n"]
        elif not read_message and not comfyui_message and not a111_message:
            infer_message = [
                "**💮 Your Guessed Prompt:**",
                f"```{result.anime_tags}```",
            ]
        elif read_message:
            infer_message = [
                "**💮 Your Guessed Prompt:**",
                f"```{result.anime_tags}```",
            ]
        else:
            infer_message = []
        if result.characters:
            characters_message = f"**🌟 Characters:** `{','.join(result.characters)}`"
        else:
            characters_message = None

        read_message = read_message or []
        if not read_message:
            if a111_message:
                read_message.extend(a111_message)
            elif comfyui_message:
                read_message.extend(comfyui_message)
        content = infer_message + read_message
        if characters_message:
            content.append(characters_message)
        prompt = telegramify_markdown.convert("\n".join(content))
        file_data.close()
        return prompt

    async def run(self):
        logger.info("Bot Start")
        bot = self.bot
        if BotSetting.proxy_address:
            from telebot import asyncio_helper

            asyncio_helper.proxy = BotSetting.proxy_address
            logger.info("Proxy tunnels are being used!")

        @bot.message_handler(
            content_types=["photo", "document"], chat_types=["private"]
        )
        async def start(message: types.Message):
            if settings.mode.only_white:
                if message.chat.id not in settings.mode.white_group:
                    return logger.info(f"White List Out {message.chat.id}")
            logger.info(f"Report in {message.chat.id} {message.from_user.id}")
            if message.photo:
                prompt = await self.tagger(file=message.photo[-1])
                await bot.reply_to(message, text=prompt, parse_mode="MarkdownV2")
            if message.document:
                prompt = await self.tagger(file=message.document)
                await bot.reply_to(message, text=prompt, parse_mode="MarkdownV2")

        @bot.message_handler(
            commands="nsfw", chat_types=["supergroup", "group", "private"]
        )
        async def nsfw(message: types.Message):
            if settings.mode.only_white:
                if message.chat.id not in settings.mode.white_group:
                    return logger.info(f"White List Out {message.chat.id}")
            contents = RandomPromptGenerator(nsfw_enabled=True).generate()
            prompt = formatting.format_text(
                formatting.mbold("⭕ NSFW Prompt"), formatting.mcode(content=contents)
            )
            return await bot.reply_to(message, text=prompt, parse_mode="MarkdownV2")

        @bot.message_handler(
            commands="sfw", chat_types=["supergroup", "group", "private"]
        )
        async def sfw(message: types.Message):
            if settings.mode.only_white:
                if message.chat.id not in settings.mode.white_group:
                    return logger.info(f"White List Out {message.chat.id}")
            contents = RandomPromptGenerator(nsfw_enabled=False).generate()
            prompt = formatting.format_text(
                formatting.mbold("🍀 SFW Prompt"), formatting.mcode(content=contents)
            )
            return await bot.reply_to(message, text=prompt, parse_mode="MarkdownV2")

        @bot.message_handler(
            commands="tag", chat_types=["supergroup", "group", "private"]
        )
        async def tag(message: types.Message):
            if settings.mode.only_white:
                if message.chat.id not in settings.mode.white_group:
                    return logger.info(f"White List Out {message.chat.id}")

            if not message.reply_to_message:
                return await bot.reply_to(
                    message,
                    text=f"🍡 please reply to an document/image with this command ({message.chat.id})",
                )
            logger.info(f"Report in {message.chat.id} {message.from_user.id}")
            reply_message = message.reply_to_message
            reply_message_ph = reply_message.photo
            reply_message_doc = reply_message.document
            if reply_message_ph:
                prompt = await self.tagger(
                    file=reply_message_ph[-1], hidden_long_text=False
                )
                return await bot.reply_to(message, text=prompt, parse_mode="MarkdownV2")
            if reply_message_doc:
                prompt = await self.tagger(
                    file=reply_message_doc, hidden_long_text=False
                )
                return await bot.reply_to(message, text=prompt, parse_mode="MarkdownV2")
            else:
                return await self.bot.reply_to(
                    message,
                    text="🍡 Please reply to an document/image with this command",
                )

        try:
            await bot.polling(
                non_stop=True, allowed_updates=util.update_types, skip_pending=True
            )
        except ApiTelegramException as e:
            logger.opt(exception=e).exception("ApiTelegramException")
        except Exception as e:
            logger.exception(e)
