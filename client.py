import asyncio
import threading
import tkinter as tk
from tkinter import scrolledtext, simpledialog, messagebox, filedialog
import signal
import queue
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("client.log"),
        logging.StreamHandler()
    ]
)

# Очереди для передачи сообщений и ошибок в основной поток
message_queue = queue.Queue()
error_queue = queue.Queue()

# Событие для сигнализации готовности цикла событий
loop_ready_event = threading.Event()

# Глобальные переменные для reader и writer
reader = None
writer = None
loop = None

# Глобальная переменная для имени пользователя
username = ""

def enqueue_message(message):
    """Добавление сообщений в очередь сообщений и логирование."""
    message_queue.put(message)
    logging.info(message)

def enqueue_error(message):
    """Добавление ошибок в очередь ошибок и логирование."""
    error_queue.put(message)
    logging.error(message)

async def receive_messages(reader):
    """Асинхронное получение сообщений от сервера."""
    try:
        while True:
            data = await reader.readuntil(b'\n')
            if not data:
                enqueue_message("Сервер закрыл соединение.")
                break
            message = data.decode('utf-8', errors='ignore').strip()
            enqueue_message(message)
    except asyncio.IncompleteReadError:
        enqueue_message("Сервер закрыл соединение.")
    except asyncio.LimitOverrunError:
        enqueue_error("Получено слишком много данных без разделителя новой строки.")
    except Exception as e:
        enqueue_error(f"Ошибка при получении сообщения: {e}")

async def send_message(writer, message):
    """Асинхронная отправка сообщения на сервер."""
    try:
        writer.write((message + '\n').encode())
        await writer.drain()
        logging.info(f"Отправлено сообщение: {message}")
    except Exception as e:
        enqueue_error(f"Ошибка при отправке сообщения: {e}")

async def main():
    """Основная асинхронная функция для подключения к серверу."""
    global reader, writer
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', 8888)
        enqueue_message("Подключено к серверу.")
        # Запуск задачи для получения сообщений
        asyncio.create_task(receive_messages(reader))
        # Установка события готовности цикла
        loop_ready_event.set()

        # Ожидание ввода имени
        while not username:
            await asyncio.sleep(0.1)
        # Отправка имени на сервер
        await send_message(writer, username)
    except ConnectionRefusedError:
        enqueue_message("Не удалось подключиться к серверу.")
    except Exception as e:
        enqueue_error(f"Ошибка подключения: {e}")

def start_async_loop():
    """Запуск асинхронного цикла событий в отдельном потоке."""
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
        loop.run_forever()
    except Exception as e:
        enqueue_error(f"Асинхронная ошибка: {e}")
    finally:
        loop.close()

def handle_exit(signum, frame):
    """Обработка сигналов завершения работы."""
    enqueue_message("Получен сигнал завершения. Закрытие клиента...")
    if writer:
        asyncio.run_coroutine_threadsafe(close_connection(), loop)
    root.quit()

async def close_connection():
    """Асинхронное закрытие соединения с сервером."""
    try:
        if writer:
            writer.close()
            await writer.wait_closed()
            enqueue_message("Соединение закрыто.")
    except Exception as e:
        enqueue_error(f"Ошибка при закрытии соединения: {e}")

def on_send_button_click():
    """Обработка нажатия кнопки 'Отправить'."""
    message = entry_widget.get()
    if message:
        if message.startswith('/'):
            messagebox.showerror("Ошибка", "Команды нельзя вводить в поле сообщений. Используйте кнопки.")
            return
        entry_widget.delete(0, tk.END)
        if loop_ready_event.is_set() and writer:
            asyncio.run_coroutine_threadsafe(send_message(writer, message), loop)
            # Отображение отправленного сообщения
            enqueue_message(f"Вы: {message}")
        else:
            enqueue_message("Не подключено к серверу или цикл событий не готов.")

def update_widgets():
    """Обновление виджетов GUI из очередей."""
    # Обновление сообщений
    while not message_queue.empty():
        msg = message_queue.get()
        text_widget.config(state='normal')
        text_widget.insert(tk.END, f"{msg}\n")
        text_widget.see(tk.END)
        text_widget.config(state='disabled')
    # Обновление ошибок
    while not error_queue.empty():
        err = error_queue.get()
        text_widget.config(state='normal')
        text_widget.insert(tk.END, f"Ошибка: {err}\n")
        text_widget.see(tk.END)
        text_widget.config(state='disabled')
    # Запланировать следующий вызов через 100 мс
    root.after(100, update_widgets)

def get_input(prompt):
    """Получение ввода от пользователя через диалоговое окно."""
    return simpledialog.askstring("Input", prompt, parent=root)

def on_create_room():
    """Обработка создания новой комнаты через кнопку."""
    room_name = get_input("Введите название новой комнаты:")
    if room_name:
        command = f"/create {room_name}"
        on_send_command(command)

def on_join_room():
    """Обработка присоединения к комнате через кнопку."""
    room_name = get_input("Введите название комнаты для присоединения:")
    if room_name:
        command = f"/join {room_name}"
        on_send_command(command)

def on_send_private_message():
    """Обработка отправки личного сообщения через кнопку."""
    target_user = get_input("Введите имя пользователя для личного сообщения:")
    if target_user:
        message = get_input("Введите сообщение:")
        if message:
            command = f"/m {target_user} {message}"
            on_send_command(command)

def on_leave_room():
    """Обработка покидания комнаты через кнопку."""
    confirm = messagebox.askyesno("Покинуть комнату", "Вы уверены, что хотите покинуть текущую комнату?")
    if confirm:
        command = "/leave"
        on_send_command(command)

def on_list_rooms():
    """Обработка запроса списка комнат через кнопку."""
    command = "/listrooms"
    on_send_command(command)

def on_list_users():
    """Обработка запроса списка пользователей через кнопку."""
    command = "/users"
    on_send_command(command)

def on_send_command(command):
    """Отправка команды на сервер."""
    if loop_ready_event.is_set() and writer:
        asyncio.run_coroutine_threadsafe(send_message(writer, command), loop)
    else:
        enqueue_message("Не подключено к серверу или цикл событий не готов.")

def prompt_username():
    """Отображение диалога для ввода имени пользователя."""
    global username
    while not username:
        username = simpledialog.askstring("Имя пользователя", "Введите ваше имя:", parent=root)
        if not username:
            messagebox.showerror("Ошибка", "Имя не может быть пустым.")

if __name__ == '__main__':
    # Создание окна клиента
    root = tk.Tk()
    root.geometry("800x600")
    root.title("Chat Client")

    # Установка приятного цвета фона (оттенки желтого)
    root.configure(bg="#FFF9C4")  # Светло-жёлтый фон

    # Запуск асинхронного цикла в отдельном потоке
    asyncio_thread = threading.Thread(target=start_async_loop, daemon=True)
    asyncio_thread.start()

    # Ожидание подключения и ввода имени
    root.after(100, prompt_username)

    # Фрейм для отображения сообщений
    text_widget = scrolledtext.ScrolledText(root, wrap=tk.WORD, height=25, width=80, state='disabled', bg="#FFFDE7", fg="#000000")
    text_widget.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

    # Фрейм для кнопок команд
    button_frame = tk.Frame(root, bg="#FFF9C4")
    button_frame.pack(padx=10, pady=5, fill=tk.X)

    # Кнопки для команд
    create_room_button = tk.Button(button_frame, text="Создать комнату", command=on_create_room, width=20, bg="#FFEB3B", fg="#000000")
    create_room_button.pack(side=tk.LEFT, padx=5, pady=5)

    join_room_button = tk.Button(button_frame, text="Присоединиться к комнате", command=on_join_room, width=25, bg="#FFEB3B", fg="#000000")
    join_room_button.pack(side=tk.LEFT, padx=5, pady=5)

    send_private_message_button = tk.Button(button_frame, text="Отправить личное сообщение", command=on_send_private_message, width=25, bg="#FFEB3B", fg="#000000")
    send_private_message_button.pack(side=tk.LEFT, padx=5, pady=5)

    leave_room_button = tk.Button(button_frame, text="Покинуть комнату", command=on_leave_room, width=15, bg="#FFEB3B", fg="#000000")
    leave_room_button.pack(side=tk.LEFT, padx=5, pady=5)

    list_rooms_button = tk.Button(button_frame, text="Список комнат", command=on_list_rooms, width=15, bg="#FFEB3B", fg="#000000")
    list_rooms_button.pack(side=tk.LEFT, padx=5, pady=5)

    list_users_button = tk.Button(button_frame, text="Список пользователей", command=on_list_users, width=20, bg="#FFEB3B", fg="#000000")
    list_users_button.pack(side=tk.LEFT, padx=5, pady=5)

    # Фрейм для ввода сообщений
    input_frame = tk.Frame(root, bg="#FFF9C4")
    input_frame.pack(padx=10, pady=5, fill=tk.X)

    # Поле ввода сообщения
    entry_widget = tk.Entry(input_frame, width=80, bg="#FFFDE7")
    entry_widget.pack(side=tk.LEFT, padx=(0,5), pady=5, expand=True, fill=tk.X)

    # Кнопка отправки сообщения
    send_button = tk.Button(input_frame, text="Отправить", command=on_send_button_click, width=10, bg="#FFEB3B", fg="#000000")
    send_button.pack(side=tk.LEFT, padx=5, pady=5)

    # Обработка сигналов
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Запуск периодического обновления виджетов
    root.after(100, update_widgets)

    # Запуск GUI
    root.mainloop()
