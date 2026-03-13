import os
import json
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import logging
import time

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('course_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class CourseClient:
    def __init__(self):
        self.base_url = os.getenv('JW_BASE_URL')
        self.username = os.getenv('JW_USERNAME')
        self.password = os.getenv('JW_PASSWORD')
        self.session = requests.Session()
        self.token = None

    def login(self):
        try:
            login_data = {'username': self.username, 'password': self.password}
            resp = self.session.post(f'{self.base_url}/api/login', json=login_data)
            if resp.status_code == 200:
                self.token = resp.json().get('token')
                self.session.headers.update({'Authorization': f'Bearer {self.token}'})
                logger.info('登录成功')
                return True
        except Exception as e:
            logger.error(f'登录失败: {e}')
        return False

    def get_timetable(self):
        try:
            resp = self.session.get(f'{self.base_url}/#/new/Table')
            soup = BeautifulSoup(resp.text, 'html.parser')
            courses = []

            for day in ['Sunday', 'monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']:
                day_courses = soup.select(f'#{day} .course-item')
                for course in day_courses:
                    text = course.get_text(strip=True)
                    style = course.get('style', '')
                    courses.append({'day': day, 'text': text, 'style': style})

            logger.info(f'获取到 {len(courses)} 门课程')
            return courses
        except Exception as e:
            logger.error(f'获取课表失败: {e}')
            return []

    def sync(self):
        if not self.token:
            if not self.login():
                return False

        courses = self.get_timetable()
        if courses:
            with open('course_data.json', 'w', encoding='utf-8') as f:
                json.dump(courses, f, ensure_ascii=False, indent=2)
            logger.info('课表同步成功')
            return True
        return False

if __name__ == '__main__':
    client = CourseClient()
    client.sync()
