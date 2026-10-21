import os
import json
import logging
import urllib.parse
import requests
from requests_oauthlib import OAuth1
from config import FATSECRET_CONSUMER_KEY, FATSECRET_CONSUMER_SECRET

AUTH_URL = "https://authentication.fatsecret.com/oauth"
API_URL = "https://platform.fatsecret.com/rest/server.api"
USER_TOKEN_FILE = "fatsecret_user.json"

class FatSecretClient:
    def __init__(self):
        self.consumer_key = FATSECRET_CONSUMER_KEY
        self.consumer_secret = FATSECRET_CONSUMER_SECRET
        self.temp_token = None
        self.temp_secret = None
        self.user_token = None
        self.user_secret = None
        self._load_user_token()

    def _load_user_token(self):
        if os.path.exists(USER_TOKEN_FILE):
            try:
                with open(USER_TOKEN_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.user_token = data.get("oauth_token")
                    self.user_secret = data.get("oauth_token_secret")
            except Exception as e:
                logging.error(f"Не удалось загрузить {USER_TOKEN_FILE}: {e}")

    def _save_user_token(self, token: str, secret: str):
        self.user_token = token
        self.user_secret = secret
        with open(USER_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"oauth_token": token, "oauth_token_secret": secret}, f, indent=2)

    def is_authorized(self) -> bool:
        return bool(self.user_token and self.user_secret)

    def get_auth_client(self) -> OAuth1:
        if self.is_authorized():
            return OAuth1(
                self.consumer_key,
                self.consumer_secret,
                resource_owner_key=self.user_token,
                resource_owner_secret=self.user_secret,
                signature_type="query"
            )
        return OAuth1(self.consumer_key, self.consumer_secret, signature_type="query")

    def start_authorization(self) -> str:
        """Получает временный request_token и возвращает ссылку для пользователя."""
        auth = OAuth1(
            self.consumer_key,
            self.consumer_secret,
            callback_uri="oob",
            signature_type="body"
        )
        res = requests.post(f"{AUTH_URL}/request_token", auth=auth, timeout=10)
        res.raise_for_status()
        
        parsed = dict(urllib.parse.parse_qsl(res.text))
        self.temp_token = parsed["oauth_token"]
        self.temp_secret = parsed["oauth_token_secret"]
        
        return f"{AUTH_URL}/authorize?oauth_token={self.temp_token}"

    def complete_authorization(self, verifier_pin: str) -> bool:
        """Обменивает PIN-код на постоянный токен пользователя."""
        if not self.temp_token or not self.temp_secret:
            raise ValueError("Сначала запустите авторизацию (start_authorization).")

        auth = OAuth1(
            self.consumer_key,
            self.consumer_secret,
            resource_owner_key=self.temp_token,
            resource_owner_secret=self.temp_secret,
            verifier=verifier_pin.strip(),
            signature_type="body"
        )
        res = requests.post(f"{AUTH_URL}/access_token", auth=auth, timeout=10)
        res.raise_for_status()
        
        parsed = dict(urllib.parse.parse_qsl(res.text))
        token = parsed["oauth_token"]
        secret = parsed["oauth_token_secret"]
        self._save_user_token(token, secret)
        return True

    def search_food(self, query: str, max_results: int = 5) -> list[dict]:
        """Ищет продукт в базе FatSecret."""
        auth = self.get_auth_client()
        params = {
            "method": "foods.search",
            "search_expression": query,
            "format": "json",
            "max_results": max_results
        }
        res = requests.get(API_URL, params=params, auth=auth, timeout=10)
        res.raise_for_status()
        data = res.json()
        foods = data.get("foods", {}).get("food", [])
        if isinstance(foods, dict):
            foods = [foods]
        return foods

    def get_food(self, food_id: str) -> dict:
        """Получает порции продукта."""
        auth = self.get_auth_client()
        params = {
            "method": "food.get.v2",
            "food_id": food_id,
            "format": "json"
        }
        res = requests.get(API_URL, params=params, auth=auth, timeout=10)
        res.raise_for_status()
        return res.json().get("food", {})

    def add_food_entry(self, food_id: str, serving_id: str, units: float, entry_name: str, meal: str = "Lunch", date_str: str = None) -> str:
        """Добавляет еду в дневник пользователя FatSecret на указанную дату (YYYY-MM-DD)."""
        if not self.is_authorized():
            raise ValueError("Пользователь FatSecret не авторизован.")
            
        auth = self.get_auth_client()
        params = {
            "method": "food_entry.create",
            "food_id": str(food_id),
            "serving_id": str(serving_id),
            "number_of_units": f"{units:.3f}",
            "food_entry_name": entry_name,
            "meal": meal,  # "Breakfast", "Lunch", "Dinner", "Other"
            "format": "json"
        }
        if date_str:
            import datetime
            target_date = datetime.date.fromisoformat(date_str)
            days = (target_date - datetime.date(1970, 1, 1)).days
            params["date"] = str(days)

        res = requests.post(API_URL, params=params, auth=auth, timeout=10)
        res.raise_for_status()
        data = res.json()
        if "error" in data:
            raise ValueError(data["error"].get("message", "Неизвестная ошибка FatSecret"))
        return data.get("food_entry_id", {}).get("value", "")
