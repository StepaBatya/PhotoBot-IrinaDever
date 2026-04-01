# -*- coding: utf-8 -*-
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import requests
import cv2
import numpy as np
import pyodbc
import configparser
import os
import sys
import codecs

# Настройка вывода для Windows (чтобы принт не падал на кириллице)
sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())

# --- 1. ИНИЦИАЛИЗАЦИЯ ---
print("--- [SYSTEM] Запуск ИС IrinaDever Professional Online ---")

try:
    config = configparser.ConfigParser()
    config.read("config.ini")
    TOKEN = config["VK"]["token"]

    # Строка подключения (проверь имя сервера SQLEXPRESS!)
    connection_string = 'DRIVER={SQL Server};SERVER=localhost\\SQLEXPRESS;DATABASE=PhotoFilterDB;Trusted_Connection=yes;'
    conn = pyodbc.connect(connection_string, autocommit=True)
    cursor = conn.cursor()
    print("[SYSTEM] Подключение к SQL Server: УСПЕШНО")
except Exception as e:
    print(f"[ERROR] Ошибка старта: {e}")
    sys.exit()

vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)
processed_messages = set()

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_setting(name):
    try:
        cursor.execute("SELECT ParamValue FROM SystemSettings WHERE ParamName = ?", (name,))
        res = cursor.fetchone()
        return res[0] if res else ""
    except: return ""

def apply_studio_fx(img, mode):
    print(f"[OPENCV] Режим обработки: {mode}")
    if mode == "beauty":
        smooth = cv2.GaussianBlur(img, (0, 0), 3)
        img = cv2.addWeighted(img, 1.5, smooth, -0.5, 0)
    elif mode == "bw_wm":
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    wm_text = get_setting('WatermarkText') or "IrinaDever Studio"
    cv2.putText(img, wm_text, (50, img.shape[0]-50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255,255,255), 2)
    return img

def upload_to_vk(img):
    success, encoded_img = cv2.imencode('.jpg', img)
    u_url = vk.photos.getMessagesUploadServer()['upload_url']
    r = requests.post(u_url, files={'photo': ('img.jpg', encoded_img.tobytes(), 'image/jpeg')}).json()
    s = vk.photos.saveMessagesPhoto(photo=r['photo'], server=r['server'], hash=r['hash'])[0]
    return f"photo{s['owner_id']}_{s['id']}"

def get_kb(role='User'):
    kb = VkKeyboard(one_time=False)
    kb.add_button("Beauty ✨", VkKeyboardColor.PRIMARY)
    kb.add_button("ЧБ 🖤", VkKeyboardColor.PRIMARY)
    kb.add_button("Коллаж 📸", VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("Мои работы 📂", VkKeyboardColor.SECONDARY)
    if role == 'Admin':
        kb.add_line()
        kb.add_button("Статистика 📊", VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

# --- 3. ЦИКЛ ОБРАБОТКИ ---
print("[SYSTEM] Ожидание сообщений...")

for event in longpoll.listen():
    if event.type == VkEventType.MESSAGE_NEW and event.to_me:
        if event.message_id in processed_messages: continue
        processed_messages.add(event.message_id)

        uid = event.user_id
        text = event.text.strip()
        print(f"\n[IN] Сообщение от {uid}: '{text}'")

        try:
            # 1. Проверка юзера
            cursor.execute("SELECT UserRole, CurrentMode FROM Users WHERE UserID=?", (uid,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("INSERT INTO Users(UserID, UserRole, CurrentMode) VALUES(?,'User','normal')", (uid, uid))
                role, mode = 'User', 'normal'
            else:
                role, mode = row

            # 2. Обработка команд
            if text.lower() in ["начать", "старт"]:
                vk.messages.send(user_id=uid, message="💎 Система Ирина Девер готова к работе!", keyboard=get_kb(role), random_id=0)

            elif text == "Мои работы 📂":
                print(f"[DB] Чтение логов для {uid}...")
                cursor.execute("""
                    SELECT TOP 5 L.LogDate, F.FilterName 
                    FROM PhotoLog L 
                    JOIN Filters F ON L.FilterID = F.FilterID 
                    WHERE L.UserID = ? 
                    ORDER BY L.LogDate DESC
                """, (uid,))
                history = cursor.fetchall()
                if history:
                    msg = "📂 Ваши последние работы:\n"
                    for h in history:
                        d = h[0].strftime('%d.%m %H:%M') if h[0] else "---"
                        msg += f"• {d} — [{h[1]}]\n"
                else:
                    msg = "✨ История пуста. Пришлите фото для обработки!"
                vk.messages.send(user_id=uid, message=msg, random_id=0)

            elif text == "Коллаж 📸":
                cursor.execute("UPDATE Users SET CurrentMode='collage_1' WHERE UserID=?", (uid,))
                vk.messages.send(user_id=uid, message="📸 Отправьте первое фото (ДО).", random_id=0)

            elif text in ["Beauty ✨", "ЧБ 🖤"]:
                new_mode = "beauty" if "Beauty" in text else "bw_wm"
                cursor.execute("UPDATE Users SET CurrentMode=? WHERE UserID=?", (new_mode, uid))
                vk.messages.send(user_id=uid, message=f"✅ Режим {text} включен. Жду фото.", random_id=0)

            elif text == "Статистика 📊" and role == 'Admin':
                cursor.execute("SELECT COUNT(*) FROM PhotoLog")
                count = cursor.fetchone()[0]
                vk.messages.send(user_id=uid, message=f"📊 Всего обработано: {count}", random_id=0)

            # 3. Обработка ФОТО
            msg_obj = vk.messages.getById(message_ids=event.message_id)['items'][0]
            if msg_data := msg_obj.get('attachments'):
                for att in msg_data:
                    if att['type'] == 'photo':
                        url = att['photo']['sizes'][-1]['url']
                        raw_photo = requests.get(url).content
                        print(f"[VK] Получено фото от {uid}")

                        if mode == 'collage_1':
                            cursor.execute("DELETE FROM PendingCollage WHERE UserID=?", (uid,))
                            cursor.execute("INSERT INTO PendingCollage (UserID, FirstPhotoUrl) VALUES (?,?)", (uid, url))
                            cursor.execute("UPDATE Users SET CurrentMode='collage_2' WHERE UserID=?", (uid,))
                            vk.messages.send(user_id=uid, message="✅ Принято. Теперь отправьте фото (ПОСЛЕ).", random_id=0)

                        elif mode == 'collage_2':
                            cursor.execute("SELECT FirstPhotoUrl FROM PendingCollage WHERE UserID=?", (uid,))
                            f_url = cursor.fetchone()[0]
                            # Тут должна быть функция make_collage (упростим для теста до эффекта)
                            img1 = cv2.imdecode(np.frombuffer(requests.get(f_url).content, np.uint8), 1)
                            img2 = cv2.imdecode(np.frombuffer(raw_photo, np.uint8), 1)
                            # Склейка
                            res = np.hstack((cv2.resize(img1, (500,500)), cv2.resize(img2, (500,500))))
                            cv2.putText(res, "BEFORE/AFTER", (20, 50), 1, 2, (255,255,255), 2)
                            
                            att_id = upload_to_vk(res)
                            cursor.execute("INSERT INTO PhotoLog (UserID, FilterID) VALUES (?, 4)", (uid,))
                            cursor.execute("UPDATE Users SET CurrentMode='normal' WHERE UserID=?", (uid,))
                            vk.messages.send(user_id=uid, message="✨ Коллаж готов!", attachment=att_id, random_id=0)

                        else: # Обычный режим
                            img = cv2.imdecode(np.frombuffer(raw_photo, np.uint8), 1)
                            res = apply_studio_fx(img, mode)
                            att_id = upload_to_vk(res)
                            f_id = 2 if mode == 'beauty' else (3 if mode == 'bw_wm' else 1)
                            cursor.execute("INSERT INTO PhotoLog (UserID, FilterID) VALUES (?, ?)", (uid, f_id))
                            vk.messages.send(user_id=uid, message="✨ Готово!", attachment=att_id, random_id=0)

        except Exception as err:
            print(f"[ERROR] Ошибка цикла: {err}")