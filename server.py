import asyncio
import signal
import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import queue
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)

# Словари для хранения подключенных клиентов и комнат чата
connected_clients = {}
chat_rooms = {'main': set()}

# Очереди для передачи сообщений в основной поток GUI
client_list_queue = queue.Queue()
room_list_queue = queue.Queue()
log_queue = queue.Queue()

def enqueue_log(message):
    """Добавление сообщений в очередь логов и логирование."""
    log_queue.put(message)
    logging.info(message)

def enqueue_client_list():
    """Обновление списка клиентов."""
    client_list = []
    for writer, name in connected_clients.items():
        addr = writer.get_extra_info('peername')
        client_list.append(f"{name} ({addr})")
    client_list_queue.put(client_list)

def enqueue_room_list():
    """Обновление списка комнат."""
    room_list = []
    for room, clients in chat_rooms.items():
        room_list.append(f"{room} ({len(clients)} участников)")
    room_list_queue.put(room_list)

def update_widgets(client_list_widget, room_list_widget, log_widget):
    """Обновление виджетов GUI из очередей."""
    # Обновление логов
    while not log_queue.empty():
        msg = log_queue.get()
        log_widget.config(state='normal')
        log_widget.insert(tk.END, f"{msg}\n")
        log_widget.see(tk.END)
        log_widget.config(state='disabled')

    # Обновление списка клиентов
    while not client_list_queue.empty():
        client_list = client_list_queue.get()
        client_list_widget.delete(0, tk.END)
        for client in client_list:
            client_list_widget.insert(tk.END, client)

    # Обновление списка комнат
    while not room_list_queue.empty():
        room_list = room_list_queue.get()
        room_list_widget.delete(0, tk.END)
        for room in room_list:
            room_list_widget.insert(tk.END, room)

    # Запланировать следующий вызов через 100 мс
    root.after(100, update_widgets, client_list_widget, room_list_widget, log_widget)

def get_current_room(writer):
    """Получение текущей комнаты клиента."""
    for room_name, clients in chat_rooms.items():
        if writer in clients:
            return room_name
    return None

async def broadcast_message(sender_writer, message, room_name):
    """Рассылка сообщения всем клиентам в комнате, кроме отправителя."""
    if room_name in chat_rooms:
        for client_writer in chat_rooms[room_name]:
            if client_writer != sender_writer:
                try:
                    client_writer.write(message.encode())
                    await client_writer.drain()
                    enqueue_log(f"Отправлено сообщение клиенту {connected_clients[client_writer]}: {message.strip()}")
                except Exception as e:
                    enqueue_log(f"Ошибка при отправке сообщения клиенту {connected_clients.get(client_writer, 'Неизвестный')}: {e}")
    else:
        sender_writer.write("Комната не найдена.\n".encode())
        await sender_writer.drain()
        enqueue_log(f"Комната '{room_name}' не найдена при попытке отправки сообщения клиенту {connected_clients[sender_writer]}.")

async def send_private_message(sender_writer, target_name, message):
    """Отправка личного сообщения конкретному пользователю."""
    target_writer = next((w for w, name in connected_clients.items() if name == target_name), None)
    if target_writer:
        try:
            sender_name = connected_clients[sender_writer]
            # Отправка сообщения самому себе
            sender_writer.write(f"Вы отправили личное сообщение {target_name}: {message}\n".encode())
            await sender_writer.drain()
            enqueue_log(f"Отправлено сообщение самому себе клиенту {sender_name}: {message}")

            # Отправка сообщения получателю
            target_writer.write(f"Личное сообщение от {sender_name}: {message}\n".encode())
            await target_writer.drain()
            enqueue_log(f"Отправлено личное сообщение клиенту {target_name}: {message}")

            # Логирование
            enqueue_log(f"{sender_name} отправил личное сообщение {target_name}: {message}")
        except Exception as e:
            enqueue_log(f"Ошибка при отправке личного сообщения от {connected_clients.get(sender_writer, 'Неизвестный')} к {target_name}: {e}")
    else:
        sender_writer.write("Пользователь не найден\n".encode())
        await sender_writer.drain()
        enqueue_log(f"Клиент {connected_clients[sender_writer]} попытался отправить личное сообщение несуществующему пользователю {target_name}.")

async def join_room(writer, room_name):
    """Присоединение клиента к комнате."""
    current_room = get_current_room(writer)
    if current_room:
        chat_rooms[current_room].remove(writer)
        if not chat_rooms[current_room]:
            del chat_rooms[current_room]
            enqueue_log(f"Комната '{current_room}' удалена, так как в ней больше нет участников.")
    if room_name not in chat_rooms:
        chat_rooms[room_name] = set()
        enqueue_log(f"Комната '{room_name}' создана автоматически при присоединении.")
    chat_rooms[room_name].add(writer)
    writer.write(f"Вы присоединились к комнате: {room_name}\n".encode())
    await writer.drain()
    enqueue_log(f"Отправлено сообщение о присоединении к комнате '{room_name}' клиенту {connected_clients[writer]}.")
    enqueue_room_list()

async def create_room(writer, room_name):
    """Создание новой комнаты."""
    if room_name in chat_rooms:
        writer.write(f"Комната '{room_name}' уже существует.\n".encode())
        await writer.drain()
        enqueue_log(f"Клиент {connected_clients[writer]} попытался создать существующую комнату '{room_name}'.")
    else:
        chat_rooms[room_name] = set()
        writer.write(f"Комната '{room_name}' создана.\n".encode())
        await writer.drain()
        enqueue_log(f"Клиент {connected_clients[writer]} создал комнату: {room_name}")
        enqueue_room_list()

async def leave_room(writer):
    """Покидание текущей комнаты."""
    current_room = get_current_room(writer)
    if current_room:
        chat_rooms[current_room].remove(writer)
        if not chat_rooms[current_room]:
            del chat_rooms[current_room]
            enqueue_log(f"Комната '{current_room}' удалена, так как в ней больше нет участников.")
        writer.write(f"Вы покинули комнату: {current_room}\n".encode())
        await writer.drain()
        enqueue_log(f"Отправлено сообщение о покидании комнаты '{current_room}' клиенту {connected_clients[writer]}.")
        enqueue_room_list()
    else:
        writer.write("Вы не находитесь в какой-либо комнате.\n".encode())
        await writer.drain()
        enqueue_log(f"Клиент {connected_clients[writer]} попытался покинуть комнату, в которой не находится.")

async def list_rooms(writer):
    """Отправка списка доступных комнат."""
    if chat_rooms:
        rooms_list = "Доступные комнаты: " + ", ".join(chat_rooms.keys()) + "\n"
    else:
        rooms_list = "Нет доступных комнат.\n"
    writer.write(rooms_list.encode())
    await writer.drain()
    enqueue_log(f"Отправлен список комнат клиенту {connected_clients[writer]}.")

async def show_current_chat(writer):
    """Отправка информации о текущей комнате."""
    room_name = get_current_room(writer)
    if room_name:
        writer.write(f"Вы находитесь в комнате: {room_name}\n".encode())
    else:
        writer.write("Вы не находитесь в какой-либо комнате.\n".encode())
    await writer.drain()
    enqueue_log(f"Отправлено сообщение о текущей комнате клиенту {connected_clients[writer]}.")

async def list_users(writer):
    """Отправка списка подключённых пользователей."""
    if connected_clients:
        users_list = "Список пользователей: " + ", ".join(connected_clients.values()) + "\n"
    else:
        users_list = "Нет подключенных пользователей.\n"
    writer.write(users_list.encode())
    await writer.drain()
    enqueue_log(f"Отправлен список пользователей клиенту {connected_clients[writer]}.")

async def show_help(writer):
    """Отправка списка доступных команд."""
    help_message = (
        "/m <user> <message> - отправить личное сообщение\n"
        "/users - показать список пользователей\n"
        "/join <room> - присоединиться к комнате\n"
        "/create <room> - создать новую комнату\n"
        "/leave - покинуть текущую комнату\n"
        "/currentchat - показать текущую комнату\n"
        "/listrooms - показать список комнат\n"
        "/upload <filename> - загрузить файл\n"
    )
    writer.write(help_message.encode())
    await writer.drain()
    enqueue_log(f"Отправлено сообщение о командах клиенту {connected_clients[writer]}.")

async def upload_file(reader, writer, filename):
    """Обработка загрузки файла от клиента."""
    try:
        writer.write("Начинаю прием файла.\n".encode())
        await writer.drain()

        # Получение размера файла
        data = await reader.readuntil(b'\n')
        filesize_str = data.decode().strip()
        filesize = int(filesize_str)
        enqueue_log(f"Получение файла '{filename}' размером {filesize} байт от {connected_clients[writer]}.")

        # Прием содержимого файла
        with open(f"received_{filename}", 'wb') as f:
            remaining = filesize
            while remaining > 0:
                chunk_size = 4096 if remaining >= 4096 else remaining
                chunk = await reader.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        writer.write(f"Файл '{filename}' успешно получен.\n".encode())
        await writer.drain()
        enqueue_log(f"Файл '{filename}' успешно получен и сохранен.")
    except Exception as e:
        writer.write(f"Ошибка при загрузке файла: {e}\n".encode())
        await writer.drain()
        enqueue_log(f"Ошибка при загрузке файла '{filename}' от {connected_clients[writer]}: {e}")

async def handle_client_connection(reader, writer):
    """Обработка подключения клиента."""
    client_address = writer.get_extra_info('peername')
    enqueue_log(f"Подключение от: {client_address}")

    try:
        # Запрос имени клиента
        writer.write("Введите ваше имя: \n".encode())
        await writer.drain()
        enqueue_log(f"Отправлено приглашение ввести имя клиенту {client_address}.")

        # Получение имени клиента
        data = await reader.read(100)
        if not data:
            raise ConnectionResetError("Клиент закрыл соединение перед отправкой имени.")
        client_name = data.decode().strip()
        if not client_name:
            writer.write("Имя не может быть пустым. Закрытие соединения.\n".encode())
            await writer.drain()
            enqueue_log(f"Клиент {client_address} отправил пустое имя. Закрытие соединения.")
            raise ValueError("Имя клиента не указано.")

        # Проверка уникальности имени
        if client_name in connected_clients.values():
            writer.write("Это имя уже занято. Закрытие соединения.\n".encode())
            await writer.drain()
            enqueue_log(f"Клиент {client_address} попытался использовать занятое имя '{client_name}'. Закрытие соединения.")
            raise ValueError("Имя клиента уже занято.")

        # Добавление клиента в список и основную комнату
        connected_clients[writer] = client_name
        chat_rooms['main'].add(writer)
        enqueue_client_list()
        enqueue_room_list()

        enqueue_log(f"{client_name} присоединился к комнате: main")

        # Приветственные сообщения
        writer.write(f"Ваше имя - {client_name}\n".encode())
        await writer.drain()
        enqueue_log(f"Отправлено имя '{client_name}' клиенту {client_address}.")

        writer.write("Вы присоединились к комнате: main\n".encode())
        await writer.drain()
        enqueue_log(f"Отправлено сообщение о присоединении к комнате main клиенту {client_name}.")

        while True:
            # Чтение сообщения от клиента
            message = await reader.read(1024)
            if not message:
                # Клиент отключился
                enqueue_log(f"Клиент {client_name} отключился.")
                break
            decoded_message = message.decode().strip()
            current_room = get_current_room(writer)
            enqueue_log(f"{client_name}@{current_room}: {decoded_message}")

            # Обработка команд
            if decoded_message.startswith('/join'):
                parts = decoded_message.split(maxsplit=1)
                if len(parts) < 2:
                    writer.write("Использование: /join <room>\n".encode())
                    await writer.drain()
                    enqueue_log(f"Клиент {client_name} использовал некорректную команду /join.")
                    continue
                room_name = parts[1]
                await join_room(writer, room_name)

            elif decoded_message.startswith('/create'):
                parts = decoded_message.split(maxsplit=1)
                if len(parts) < 2:
                    writer.write("Использование: /create <room>\n".encode())
                    await writer.drain()
                    enqueue_log(f"Клиент {client_name} использовал некорректную команду /create.")
                    continue
                room_name = parts[1]
                await create_room(writer, room_name)

            elif decoded_message.startswith('/leave'):
                await leave_room(writer)

            elif decoded_message.startswith('/listrooms'):
                await list_rooms(writer)

            elif decoded_message.startswith('/currentchat'):
                await show_current_chat(writer)

            elif decoded_message.startswith('/m'):
                parts = decoded_message.split(maxsplit=2)
                if len(parts) < 3:
                    writer.write("Использование: /m <user> <message>\n".encode())
                    await writer.drain()
                    enqueue_log(f"Клиент {client_name} использовал некорректную команду /m.")
                    continue
                target_name = parts[1]
                private_message = parts[2]
                await send_private_message(writer, target_name, private_message)

            elif decoded_message.startswith('/users'):
                await list_users(writer)

            elif decoded_message.startswith('/help'):
                await show_help(writer)

            elif decoded_message.startswith('/upload'):
                # Обработка загрузки файла
                parts = decoded_message.split(maxsplit=1)
                if len(parts) < 2:
                    writer.write("Использование: /upload <filename>\n".encode())
                    await writer.drain()
                    enqueue_log(f"Клиент {client_name} использовал некорректную команду /upload.")
                    continue
                filename = parts[1]
                await upload_file(reader, writer, filename)

            else:
                if current_room:
                    await broadcast_message(writer, f"{client_name}: {decoded_message}\n", current_room)
                else:
                    writer.write("Вы не находитесь в комнате.\n".encode())
                    await writer.drain()
                    enqueue_log(f"Клиент {client_name} отправил сообщение без присоединения к комнате.")

    except ConnectionResetError as cre:
        enqueue_log(f"Соединение сброшено клиентом {client_address}: {cre}")
    except Exception as e:
        enqueue_log(f"Ошибка при обработке клиента {client_address}: {e}")
    finally:
        await disconnect_client(writer, client_address)

async def disconnect_client(writer, client_address):
    """Отключение клиента и очистка данных."""
    client_name = connected_clients.pop(writer, "Неизвестный")
    await leave_room(writer)
    try:
        writer.close()
        await writer.wait_closed()
        enqueue_log(f"Соединение с клиентом {client_name} ({client_address}) закрыто.")
    except Exception as e:
        enqueue_log(f"Ошибка при закрытии соединения с {client_address}: {e}")
    enqueue_log(f"Отключение: {client_address}")
    enqueue_client_list()
    enqueue_room_list()

async def start_server():
    """Запуск сервера."""
    server = await asyncio.start_server(handle_client_connection, '127.0.0.1', 8888)
    enqueue_log("Сервер запущен и слушает порт 8888")
    async with server:
        await server.serve_forever()

def server_thread():
    """Запуск серверного цикла в отдельном потоке."""
    try:
        asyncio.run(start_server())
    except Exception as e:
        enqueue_log(f"Серверная ошибка: {e}")

# Обработка сигналов для корректного завершения работы
def handle_exit(signum, frame):
    enqueue_log("Получен сигнал завершения. Остановка сервера...")
    # Остановка asyncio цикла
    loop = asyncio.get_event_loop()
    loop.call_soon_threadsafe(loop.stop)

if __name__ == '__main__':
    # Создание окна сервера
    root = tk.Tk()
    root.title("Сервер чата")

    # Настройка размера окна
    root.geometry("1200x700")

    # Фрейм для списка клиентов
    clients_frame = tk.Frame(root)
    clients_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=True)

    # Метка для списка клиентов
    clients_label = tk.Label(clients_frame, text="Подключённые клиенты", font=("Arial", 12, "bold"))
    clients_label.pack(pady=(0,5))

    # Список клиентов
    client_list_widget = tk.Listbox(clients_frame, width=40, bg="#FFFDE7", fg="#000000")
    client_list_widget.pack(fill=tk.BOTH, expand=True)

    # Фрейм для списка комнат
    rooms_frame = tk.Frame(root)
    rooms_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=True)

    # Метка для списка комнат
    rooms_label = tk.Label(rooms_frame, text="Комнаты чата", font=("Arial", 12, "bold"))
    rooms_label.pack(pady=(0,5))

    # Список комнат
    room_list_widget = tk.Listbox(rooms_frame, width=40, bg="#FFFDE7", fg="#000000")
    room_list_widget.pack(fill=tk.BOTH, expand=True)

    # Фрейм для логов
    logs_frame = tk.Frame(root)
    logs_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=True)

    # Метка для логов
    logs_label = tk.Label(logs_frame, text="Логи сервера", font=("Arial", 12, "bold"))
    logs_label.pack(pady=(0,5))

    # Виджет для логов
    log_widget = scrolledtext.ScrolledText(logs_frame, wrap=tk.WORD, width=60, height=30, state='disabled', bg="#FFFDE7", fg="#000000")
    log_widget.pack(fill=tk.BOTH, expand=True)

    # Запуск серверного потока
    threading.Thread(target=server_thread, daemon=True).start()

    # Обработка сигналов
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    # Запуск периодического обновления виджетов
    root.after(100, update_widgets, client_list_widget, room_list_widget, log_widget)

    # Запуск GUI
    root.mainloop()
