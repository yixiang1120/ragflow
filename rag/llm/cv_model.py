#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from openai.lib.azure import AzureOpenAI
from zhipuai import ZhipuAI
import io
from abc import ABC
from ollama import Client
from PIL import Image
from openai import OpenAI
import os
import base64
from io import BytesIO
import json
import requests

from rag.nlp import is_english
from api.utils import get_uuid
from api.utils.file_utils import get_project_base_directory
from google.generativeai import client, GenerativeModel, GenerationConfig


class Base(ABC):
    def __init__(self, key, model_name):
        pass

    def describe(self, image, max_tokens=16385):
        raise NotImplementedError("Please implement encode method!")
        
    def chat(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]
        try:
            for his in history:
                if his["role"] == "user":
                    his["content"] = self.chat_prompt(his["content"], image)

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=history,
                max_tokens=gen_conf.get("max_tokens", 16385),
                temperature=gen_conf.get("temperature", 0.3),
                top_p=gen_conf.get("top_p", 0.7)
            )
            return response.choices[0].message.content.strip(), response.usage.total_tokens
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

        ans = ""
        tk_count = 0
        try:
            for his in history:
                if his["role"] == "user":
                    his["content"] = self.chat_prompt(his["content"], image)

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=history,
                max_tokens=gen_conf.get("max_tokens", 16385),
                temperature=gen_conf.get("temperature", 0.3),
                top_p=gen_conf.get("top_p", 0.7),
                stream=True
            )
            for resp in response:
                if not resp.choices[0].delta.content: continue
                delta = resp.choices[0].delta.content
                ans += delta
                if resp.choices[0].finish_reason == "length":
                    ans += "...\nFor the content length reason, it stopped, continue?" if is_english(
                        [ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
                    tk_count = resp.usage.total_tokens
                if resp.choices[0].finish_reason == "stop": tk_count = resp.usage.total_tokens
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield tk_count
        
    def image2base64(self, image):
        if isinstance(image, bytes):
            return base64.b64encode(image).decode("utf-8")
        if isinstance(image, BytesIO):
            return base64.b64encode(image.getvalue()).decode("utf-8")
        buffered = BytesIO()
        try:
            image.save(buffered, format="JPEG")
        except Exception as e:
            image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    def prompt(self, b64):
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        },
                    },
                    {
                        "text": """回答使用繁體中文，你將會收到一張圖片，其中可能包含圖表、數據或其他視覺資訊。你的任務是：
                        1. 識別並列出圖片中的所有資訊，包括但不限於標題、軸標籤、圖例、數據點、年份、比例和圖表名稱。
                        2. 注重細節，確保每個可見數據點或文字內容都被提取出來。請列出圖表中每個數據值（如果有）。
                        3. 分段整理資訊，如標題、圖例、橫軸和縱軸的標籤和單位、具體的數據點（按年份）等。
                        4. 不要加入推測性或解釋性內容，僅列出圖片中所能直接觀察到的資訊。
                        5. 輸出的語言必須與圖片中的語言一致。如果圖片中的文字為中文，輸出也必須為中文；如果為英文，輸出也必須為英文。"""
                        if self.lang.lower() == "chinese" else
                        """Answer Traditional Chinese. You will receive an image that may contain charts, data, or other visual information. Your task is to:
                        1. Identify and list all the information in the image, including but not limited to titles, axis labels, legends, data points, years, scales, and chart names.
                        2. Pay attention to detail to ensure that every visible data point or text content is extracted. Please list the data values for each year (if any) shown in the chart.
                        3. Organize the information into sections, such as titles, legends, labels and units of the horizontal and vertical axes, specific data points (by year), etc.
                        4. Do not add speculative or interpretative content; only list the information that can be directly observed in the image.
                        5. The output language must match the language in the image. If the text in the image is in Chinese, the output must also be in Chinese; if it is in English, the output must also be in English.""",
                    },
                ],
            }
        ]


    def chat_prompt(self, text, b64):
        return [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            },
            {
                "type": "text",
                "text": text
            },
        ]


class GptV4(Base):
    def __init__(self, key, model_name="gpt-4-vision-preview", lang="Chinese", base_url="https://api.openai.com/v1"):
        if not base_url: base_url="https://api.openai.com/v1"
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model_name = model_name
        self.lang = lang

    def chat_prompt(self, text, b64):
         return [
             {"image": f"{b64}"},
             {"text": text},
         ]
    
    def describe(self, image, max_tokens=16385):
        b64 = self.image2base64(image)
        prompt = self.prompt(b64)
        for i in range(len(prompt)):
            for c in prompt[i]["content"]:
                if "text" in c: c["type"] = "text"

        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=prompt,
            max_tokens=max_tokens,
        )
        return res.choices[0].message.content.strip(), res.usage.total_tokens
    """
    def chat(self, system, history, gen_conf, image=""):
         from http import HTTPStatus
         from dashscope import MultiModalConversation
         if system:
             history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

         for his in history:
             if his["role"] == "user":
                 his["content"] = self.chat_prompt(his["content"], image)
         response = MultiModalConversation.call(model=self.model_name, messages=history,
                                                max_tokens=gen_conf.get("max_tokens", 3072),
                                                temperature=gen_conf.get("temperature", 0.3),
                                                top_p=gen_conf.get("top_p", 0.7))

         ans = ""
         tk_count = 0
         if response.status_code == HTTPStatus.OK:
             ans += response.output.choices[0]['message']['content']
             tk_count += response.usage.total_tokens
             if response.output.choices[0].get("finish_reason", "") == "length":
                 ans += "...\nFor the content length reason, it stopped, continue?" if is_english(
                     [ans]) else "······\nu由於長度的原因，回答被截斷了，要繼續嗎？"
             return ans, tk_count

         return "**ERROR**: " + response.message, tk_count

    def chat_streamly(self, system, history, gen_conf, image=""):
         from http import HTTPStatus
         from dashscope import MultiModalConversation
         if system:
             history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

         for his in history:
             if his["role"] == "user":
                 his["content"] = self.chat_prompt(his["content"], image)

         ans = ""
         tk_count = 0
         try:
             response = MultiModalConversation.call(model=self.model_name, messages=history,
                                                    max_tokens=gen_conf.get("max_tokens", 2048),
                                                    temperature=gen_conf.get("temperature", 0.3),
                                                    top_p=gen_conf.get("top_p", 0.7),
                                                    stream=True)
             for resp in response:
                 if resp.status_code == HTTPStatus.OK:
                     ans = resp.output.choices[0]['message']['content']
                     tk_count = resp.usage.total_tokens
                     if resp.output.choices[0].get("finish_reason", "") == "length":
                         ans += "...\nFor the content length reason, it stopped, continue?" if is_english(
                             [ans]) else "······\nu由於長度的原因，回答被截斷了，要繼續嗎？"
                     yield ans
                 else:
                     yield ans + "\n**ERROR**: " + resp.message if str(resp.message).find(
                         "Access") < 0 else "Out of credit. Please set the API key in **settings > Model providers.**"
         except Exception as e:
             yield ans + "\n**ERROR**: " + str(e)

         yield tk_count
         """

class AzureGptV4(Base):
    def __init__(self, key, model_name, lang="Chinese", **kwargs):
        self.client = AzureOpenAI(api_key=key, azure_endpoint=kwargs["base_url"], api_version="2024-02-01")
        self.model_name = model_name
        self.lang = lang

    def describe(self, image, max_tokens=16385):
        b64 = self.image2base64(image)
        prompt = self.prompt(b64)
        for i in range(len(prompt)):
            for c in prompt[i]["content"]:
                if "text" in c: c["type"] = "text"

        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=prompt,
            max_tokens=max_tokens,
        )
        return res.choices[0].message.content.strip(), res.usage.total_tokens


class QWenCV(Base):
    def __init__(self, key, model_name="qwen-vl-chat-v1", lang="Chinese", **kwargs):
        import dashscope
        dashscope.api_key = key
        self.model_name = model_name
        self.lang = lang

    def prompt(self, binary):
        # stupid as hell
        tmp_dir = get_project_base_directory("tmp")
        if not os.path.exists(tmp_dir):
            os.mkdir(tmp_dir)
        path = os.path.join(tmp_dir, "%s.jpg" % get_uuid())
        Image.open(io.BytesIO(binary)).save(path)
        return [
            {
                "role": "user",
                "content": [
                    {
                        "image": f"file://{path}"
                    },
                    {
                        "text": """回答繁體中文，你將會收到一張圖片，其中可能包含圖表、數據或其他視覺資訊。你的任務是：
                        1. 識別並列出圖片中的所有資訊**，包括但不限於標題、軸標籤、圖例、數據點、年份、比例和圖表名稱。
                        2. 注重細節，確保每個可見數據點或文字內容都被提取出來。請列出圖表中每個年份的數據值（如果有）。
                        3. 分段整理資訊，如標題、圖例、橫軸和縱軸的標籤和單位、具體的數據點（按年份）等。
                        4. 不要加入推測性或解釋性內容，僅列出圖片中所能直接觀察到的資訊。
                        5. 輸出的語言必須與圖片中的語言一致。如果圖片中的文字為中文，輸出也必須為中文；如果為英文，輸出也必須為英文。"""
                        if self.lang.lower() == "chinese" else
                        """Please describe the contents of the image in detail, and adhere to the following guidelines:
                            1. If the image is of a chart type, please extract the data information; if there is no data information in the image, then describe aspects such as time, location, people, events, and the emotions of the people.
                            2. Ensure that the output is in the same language as the identified image content, for example, if the identified fields are in English, the output must be in English.
                            3. Do not interpret and output irrelevant text, directly output the contents of the image.""",
                    },
                ],
            }
        ]

    def chat_prompt(self, text, b64):
        return [
            {"image": f"{b64}"},
            {"text": text},
        ]
    
    def describe(self, image, max_tokens=2048):
        from http import HTTPStatus
        from dashscope import MultiModalConversation
        response = MultiModalConversation.call(model=self.model_name,
                                               messages=self.prompt(image))
        if response.status_code == HTTPStatus.OK:
            return response.output.choices[0]['message']['content'][0]["text"], response.usage.output_tokens
        return response.message, 0
    """
    def chat(self, system, history, gen_conf, image=""):
        from http import HTTPStatus
        from dashscope import MultiModalConversation
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

        for his in history:
            if his["role"] == "user":
                his["content"] = self.chat_prompt(his["content"], image)
        response = MultiModalConversation.call(model=self.model_name, messages=history,
                                               max_tokens=gen_conf.get("max_tokens", 1000),
                                               temperature=gen_conf.get("temperature", 0.3),
                                               top_p=gen_conf.get("top_p", 0.7))

        ans = ""
        tk_count = 0
        if response.status_code == HTTPStatus.OK:
            ans += response.output.choices[0]['message']['content']
            tk_count += response.usage.total_tokens
            if response.output.choices[0].get("finish_reason", "") == "length":
                ans += "...\nFor the content length reason, it stopped, continue?" if is_english(
                    [ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
            return ans, tk_count

        return "**ERROR**: " + response.message, tk_count

    def chat_streamly(self, system, history, gen_conf, image=""):
        from http import HTTPStatus
        from dashscope import MultiModalConversation
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

        for his in history:
            if his["role"] == "user":
                his["content"] = self.chat_prompt(his["content"], image)

        ans = ""
        tk_count = 0
        try:
            response = MultiModalConversation.call(model=self.model_name, messages=history,
                                                   max_tokens=gen_conf.get("max_tokens", 1000),
                                                   temperature=gen_conf.get("temperature", 0.3),
                                                   top_p=gen_conf.get("top_p", 0.7),
                                                   stream=True)
            for resp in response:
                if resp.status_code == HTTPStatus.OK:
                    ans = resp.output.choices[0]['message']['content']
                    tk_count = resp.usage.total_tokens
                    if resp.output.choices[0].get("finish_reason", "") == "length":
                        ans += "...\nFor the content length reason, it stopped, continue?" if is_english(
                            [ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
                    yield ans
                else:
                    yield ans + "\n**ERROR**: " + resp.message if str(resp.message).find(
                        "Access") < 0 else "Out of credit. Please set the API key in **settings > Model providers.**"
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield tk_count
"""

class Zhipu4V(Base):
    def __init__(self, key, model_name="glm-4v", lang="Chinese", **kwargs):
        self.client = ZhipuAI(api_key=key)
        self.model_name = model_name
        self.lang = lang

    def describe(self, image, max_tokens=2048):
        b64 = self.image2base64(image)

        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=self.prompt(b64),
            max_tokens=max_tokens,
        )
        return res.choices[0].message.content.strip(), res.usage.total_tokens

    def chat(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]
        try:
            for his in history:
                if his["role"] == "user":
                    his["content"] = self.chat_prompt(his["content"], image)

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=history,
                max_tokens=gen_conf.get("max_tokens", 1000),
                temperature=gen_conf.get("temperature", 0.3),
                top_p=gen_conf.get("top_p", 0.7)
            )
            return response.choices[0].message.content.strip(), response.usage.total_tokens
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

        ans = ""
        tk_count = 0
        try:
            for his in history:
                if his["role"] == "user":
                    his["content"] = self.chat_prompt(his["content"], image)

            response = self.client.chat.completions.create(
                model=self.model_name, 
                messages=history,
                max_tokens=gen_conf.get("max_tokens", 1000),
                temperature=gen_conf.get("temperature", 0.3),
                top_p=gen_conf.get("top_p", 0.7),
                stream=True
            )
            for resp in response:
                if not resp.choices[0].delta.content: continue
                delta = resp.choices[0].delta.content
                ans += delta
                if resp.choices[0].finish_reason == "length":
                    ans += "...\nFor the content length reason, it stopped, continue?" if is_english(
                        [ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
                    tk_count = resp.usage.total_tokens
                if resp.choices[0].finish_reason == "stop": tk_count = resp.usage.total_tokens
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield tk_count


class OllamaCV(Base):
    def __init__(self, key, model_name, lang="Chinese", **kwargs):
        self.client = Client(host=kwargs["base_url"])
        self.model_name = model_name
        self.lang = lang

    def describe(self, image, max_tokens=16385):
        prompt = self.prompt("")
        try:
            options = {"num_predict": max_tokens}
            response = self.client.generate(
                model=self.model_name,
                prompt=prompt[0]["content"][1]["text"],
                images=[image],
                options=options
            )
            ans = response["response"].strip()
            return ans, 128
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]
            
        try:
            for his in history:
                if his["role"] == "user":
                    his["images"] = [image]
            options = {}
            if "temperature" in gen_conf: options["temperature"] = gen_conf["temperature"]
            if "max_tokens" in gen_conf: options["num_predict"] = gen_conf["max_tokens"]
            if "top_p" in gen_conf: options["top_k"] = gen_conf["top_p"]
            if "presence_penalty" in gen_conf: options["presence_penalty"] = gen_conf["presence_penalty"]
            if "frequency_penalty" in gen_conf: options["frequency_penalty"] = gen_conf["frequency_penalty"]
            response = self.client.chat(
                model=self.model_name,
                messages=history,
                options=options,
                keep_alive=-1
            )

            ans = response["message"]["content"].strip()
            return ans, response["eval_count"] + response.get("prompt_eval_count", 0)
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

        for his in history:
            if his["role"] == "user":
                his["images"] = [image]
        options = {}
        if "temperature" in gen_conf: options["temperature"] = gen_conf["temperature"]
        if "max_tokens" in gen_conf: options["num_predict"] = gen_conf["max_tokens"]
        if "top_p" in gen_conf: options["top_k"] = gen_conf["top_p"]
        if "presence_penalty" in gen_conf: options["presence_penalty"] = gen_conf["presence_penalty"]
        if "frequency_penalty" in gen_conf: options["frequency_penalty"] = gen_conf["frequency_penalty"]
        ans = ""
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=history,
                stream=True,
                options=options,
                keep_alive=-1
            )
            for resp in response:
                if resp["done"]:
                    yield resp.get("prompt_eval_count", 0) + resp.get("eval_count", 0)
                ans += resp["message"]["content"]
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)
        yield 0


class LocalAICV(Base):
    def __init__(self, key, model_name, base_url, lang="Chinese"):
        self.client = OpenAI(api_key="empty", base_url=base_url)
        self.model_name = model_name.split("___")[0]
        self.lang = lang

    def describe(self, image, max_tokens=300):
        b64 = self.image2base64(image)
        prompt = self.prompt(b64)
        for i in range(len(prompt)):
            for c in prompt[i]["content"]:
                if "text" in c:
                    c["type"] = "text"

        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=prompt,
            max_tokens=max_tokens,
        )
        return res.choices[0].message.content.strip(), res.usage.total_tokens


class XinferenceCV(Base):
    def __init__(self, key, model_name="", lang="Chinese", base_url=""):
        self.client = OpenAI(api_key="xxx", base_url=base_url)
        self.model_name = model_name
        self.lang = lang

    def describe(self, image, max_tokens=2048):
        b64 = self.image2base64(image)

        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=self.prompt(b64),
            max_tokens=max_tokens,
        )
        return res.choices[0].message.content.strip(), res.usage.total_tokens

class GeminiCV(Base):
    def __init__(self, key, model_name="gemini-1.0-pro-vision-latest", lang="Chinese", **kwargs):
        from google.generativeai import client, GenerativeModel, GenerationConfig
        client.configure(api_key=key)
        _client = client.get_default_generative_client()
        self.model_name = model_name
        self.model = GenerativeModel(model_name=self.model_name)
        self.model._client = _client
        self.lang = lang 

    def describe(self, image, max_tokens=2048):
        from PIL.Image import open
        gen_config = {'max_output_tokens':max_tokens}
        prompt = "请用中文详细描述一下图中的内容，比如时间，地点，人物，事情，人物心情等，如果有数据请提取出数据。" if self.lang.lower() == "chinese" else \
            "Please describe the content of this picture, like where, when, who, what happen. If it has number data, please extract them out."
        b64 = self.image2base64(image) 
        img = open(BytesIO(base64.b64decode(b64))) 
        input = [prompt,img]
        res = self.model.generate_content(
            input,
            generation_config=gen_config,
        )
        return res.text,res.usage_metadata.total_token_count

    def chat(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]
        try:
            for his in history:
                if his["role"] == "assistant":
                    his["role"] = "model"
                    his["parts"] = [his["content"]]
                    his.pop("content")
                if his["role"] == "user":
                    his["parts"] = [his["content"]]
                    his.pop("content")
            history[-1]["parts"].append(f"data:image/jpeg;base64," + image)

            response = self.model.generate_content(history, generation_config=GenerationConfig(
                max_output_tokens=gen_conf.get("max_tokens", 1000), temperature=gen_conf.get("temperature", 0.3),
                top_p=gen_conf.get("top_p", 0.7)))

            ans = response.text
            return ans, response.usage_metadata.total_token_count
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    def chat_streamly(self, system, history, gen_conf, image=""):
        if system:
            history[-1]["content"] = system + history[-1]["content"] + "user query: " + history[-1]["content"]

        ans = ""
        tk_count = 0
        try:
            for his in history:
                if his["role"] == "assistant":
                    his["role"] = "model"
                    his["parts"] = [his["content"]]
                    his.pop("content")
                if his["role"] == "user":
                    his["parts"] = [his["content"]]
                    his.pop("content")
            history[-1]["parts"].append(f"data:image/jpeg;base64," + image)

            response = self.model.generate_content(history, generation_config=GenerationConfig(
                max_output_tokens=gen_conf.get("max_tokens", 1000), temperature=gen_conf.get("temperature", 0.3),
                top_p=gen_conf.get("top_p", 0.7)), stream=True)

            for resp in response:
                if not resp.text: continue
                ans += resp.text
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield response._chunks[-1].usage_metadata.total_token_count


"""
class OpenRouterCV(Base):
    def __init__(
        self,
        key,
        model_name,
        lang="Chinese",
        base_url="https://openrouter.ai/api/v1/chat/completions",
    ):
        self.model_name = model_name
        self.lang = lang
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.key = key

    def describe(self, image, max_tokens=300):
        b64 = self.image2base64(image)
        response = requests.post(
            url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.key}",
            },
            data=json.dumps(
                {
                    "model": self.model_name,
                    "messages": self.prompt(b64),
                    "max_tokens": max_tokens,
                }
            ),
        )
        response = response.json()
        return (
            response["choices"][0]["message"]["content"].strip(),
            response["usage"]["total_tokens"],
        )

    def prompt(self, b64):
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "请用中文详细描述一下图中的内容，比如时间，地点，人物，事情，人物心情等，如果有数据请提取出数据。"
                            if self.lang.lower() == "chinese"
                            else "Please describe the content of this picture, like where, when, who, what happen. If it has number data, please extract them out."
                        ),
                    },
                ],
            }
        ]
"""

class LocalCV(Base):
    def __init__(self, key, model_name="glm-4v", lang="Chinese", **kwargs):
        pass

    def describe(self, image, max_tokens=2048):
        return "", 0


class NvidiaCV(Base):
    def __init__(
        self,
        key,
        model_name,
        lang="Chinese",
        base_url="https://ai.api.nvidia.com/v1/vlm",
    ):
        if not base_url:
            base_url = ("https://ai.api.nvidia.com/v1/vlm",)
        self.lang = lang
        factory, llm_name = model_name.split("/")
        if factory != "liuhaotian":
            self.base_url = os.path.join(base_url, factory, llm_name)
        else:
            self.base_url = os.path.join(
                base_url, "community", llm_name.replace("-v1.6", "16")
            )
        self.key = key

    def describe(self, image, max_tokens=1024):
        b64 = self.image2base64(image)
        response = requests.post(
            url=self.base_url,
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
            json={
                "messages": self.prompt(b64),
                "max_tokens": max_tokens,
            },
        )
        response = response.json()
        return (
            response["choices"][0]["message"]["content"].strip(),
            response["usage"]["total_tokens"],
        )

    def prompt(self, b64):
        return [
            {
                "role": "user",
                "content": (
                    "请用中文详细描述一下图中的内容，比如时间，地点，人物，事情，人物心情等，如果有数据请提取出数据。"
                    if self.lang.lower() == "chinese"
                    else "Please describe the content of this picture, like where, when, who, what happen. If it has number data, please extract them out."
                )
                + f' <img src="data:image/jpeg;base64,{b64}"/>',
            }
        ]

    def chat_prompt(self, text, b64):
        return [
            {
                "role": "user",
                "content": text + f' <img src="data:image/jpeg;base64,{b64}"/>',
            }
        ]


class LmStudioCV(LocalAICV):
    def __init__(self, key, model_name, base_url, lang="Chinese"):
        if not base_url:
            raise ValueError("Local llm url cannot be None")
        if base_url.split('/')[-1] != 'v1':
            self.base_url = os.path.join(base_url,'v1')
        self.client = OpenAI(api_key="lm-studio", base_url=self.base_url)
        self.model_name = model_name
        self.lang = lang
