import datetime

import requests
import urllib3.util
from astrbot.api.all import *
from astrbot.api.event import filter, AstrMessageEvent

GEO_API_PATH = "/geo/v2/city/lookup"
WEATHER_LOOKUP_API_PATH = "/v7/weather"

AVAILABLE_PATH_PARAM = [
    "now", "3d", "7d", "10d", "15d", "30d", "24h", "72h", "168h"
]

HELP_TEXT = """使用方法：/天气 <城市> [时间]
可使用的时间格式如下：
"""+", ".join(AVAILABLE_PATH_PARAM)

KEY_TEXT = {
    "obsTime": lambda x: "观测时间: "+x,
    "fxTime": lambda x: "时间: "+x,
    "fxDate": lambda x: "日期: "+x,
    "temp": lambda x: "温度: {}°C".format(x),
    "tempMax": lambda x: "最高温度: {}°C".format(x),
    "tempMin": lambda x: "最低温度: {}°C".format(x),
    "feelsLike": lambda x: "体感温度: {}°C".format(x),
    "text": lambda x:"天气: {}".format(x),
    "textDay": lambda x:"白天天气: {}".format(x),
    "textNight": lambda x:"夜间天气: {}".format(x),
    "windDir": lambda x:"风向: {}".format(x),
    "windScale": lambda x:"风力: {}级".format(x),
    "windScaleDay": lambda x:"白天风力: {}级".format(x),
    "windScaleNight": lambda x:"夜间风力: {}级".format(x),
    "humidity": lambda x:"湿度: {}%".format(x),
    "uvIndex": lambda x:"紫外线: {}级".format(x),
    "pressure": lambda x:"气压: {}百帕".format(x)
}

def parse_weather_data(json_data) -> list[str]:
    entries = []
    for k,v in json_data.items():
        if k in KEY_TEXT:
            entries.append(KEY_TEXT[k](v))
    return entries

class Main(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        # 加载配置文件
        self.config = config
        self.wake_prefix = [""]
        self.wake_prefix.extend(self.context.get_config().get('wake_prefix', []))
        # 初始化实例变量
        qweather_api_host = self.config.get("qweather_api_host", "")
        try:
            self.base_url = str(urllib3.util.parse_url("https://" + qweather_api_host))
        except Exception as e:
            logger.error(f"初始化api_host失败: {e}")

        self.qweather_project_id = self.config.get("qweather_project_id", "")
        self.qweather_credential_id = self.config.get("qweather_credential_id", "")
        self.qweather_private_key = self.config.get("qweather_private_key", "")

    def _gen_jwt(self):
        import jwt
        now = datetime.datetime.now(datetime.UTC)
        headers = {
            "alg": "EdDSA",
            "kid": self.qweather_credential_id
        }
        payload = {
            "sub": self.qweather_project_id,  # 面向的用户
            "iat": int(now.timestamp()) - 30,  # 签发时间
            "exp": int((now + datetime.timedelta(minutes=30)).timestamp()),  # 30分钟后过期
        }
        return jwt.encode(payload=payload, headers=headers, key=self.qweather_private_key, algorithm="Ed25519")

    def _get_location(self, jwt, msg: str) :
        # 城市搜索
        url = self.base_url + GEO_API_PATH
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/json"
        }
        params = {
            "location": msg
        }
        response = requests.get(
            url,
            headers=headers,
            params=params
        )

        if response.status_code == 200:
            country = response.json()["location"][0]["country"]
            adm1 = response.json()["location"][0]["adm1"]
            adm2 = response.json()["location"][0]["adm2"]
            name = response.json()["location"][0]["name"]
            location_id = response.json()["location"][0]["id"]
            if country == "中国":  # 中国内地
                if adm2 == name:  # 精确到中国的“市”的情况
                    location = adm1 + adm2 + "市"
                else:
                    location = adm1 + adm2 + "市" + name + "区"
            else:  # 其他国家或地区
                if country == adm1:
                    location = country
                if adm1 == adm2:
                    location = country + " " + adm1
                if adm2 == name:
                    location = country + " " + adm1 + " " + adm2
                else:
                    location = country + " " + adm1 + " " + adm2 + " " + name
            return response, {
                "location": location,
                "location_id": location_id
            }
        return response, None

    def _get_weather(self, jwt, location_id, path_param):
        url = self.base_url + WEATHER_LOOKUP_API_PATH + "/" + path_param
        headers = {
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/json"
        }
        params = {
            "location": location_id,
            "lang": "zh-hans"
        }

        response = requests.get(
            url,
            headers=headers,
            params=params
        )

        weather_data = []
        if response.status_code == 200:
            response_json = response.json()
            if "now" in response_json:
                weather_data.extend(parse_weather_data(response_json["now"]))
            if "hourly" in response_json:
                for d in response_json["hourly"]:
                    weather_data.append(",".join(parse_weather_data(d)))
            if "daily" in response_json:
                for d in response_json["daily"]:
                    weather_data.append(", ".join(parse_weather_data(d)))
        return response, weather_data

    @filter.command("天气", alias={"weather"})
    async def get_weather(self, event: AstrMessageEvent):
        args = event.message_str
        args_split = args.strip().split()
        if not args_split or len(args_split) < 2:
            yield event.plain_result("⚠️ 请输入正确的指令\n"+HELP_TEXT)
            return
        city = args_split[1]
        if city == "help":
            yield event.plain_result(HELP_TEXT)
            return

        param = args_split[2] if len(args_split) > 2 else None
        if param is not None and param not in AVAILABLE_PATH_PARAM:
            yield event.plain_result("⚠️ 时间格式不正确\n"+HELP_TEXT)
            return
        if param is None:
            param = "now"

        jwt = self._gen_jwt()
        response, location_data = self._get_location(jwt, city)
        if not location_data:
            logger.error(f"❌ 查询{city}失败: "+response.text)
            yield event.plain_result(f"❌ {city} 城市错误")
            return

        response, weather_data = self._get_weather(jwt, location_data["location_id"], param)
        if not weather_data:
            logger.error(f"❌ 查询天气失败: {location_data}\n"+response.text)
            yield event.plain_result(f"❌ 查询{location_data["location"]}天气失败")
            return

        lines = [f"城市: {location_data["location"]}"] + weather_data
        yield event.plain_result("\n".join(lines))
        return