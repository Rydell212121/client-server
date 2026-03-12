import socket
import os
import sys


HOST = "127.0.0.1"
PORT = 8080
TIMEOUT = 5


class Exit(Exception):  ...     # создал свою ошибку что красиво было


def upload_file(path1: str, path2: str, socket: socket.socket):
    """Эта функция реализует загружение файла на сервер.
    Args:
        path1: это путь к клиентскому файлу, который нужно отправить
        path2: это путь, по которому будет располагаться файл на сервере
        socket: сокет, по которому происходит общение
    Функция возвращает небольшое сообщение, если все хорошо или вызывает ошибку."""

    if not os.path.exists(path1):   # проверка на существования файла
        raise ValueError("\nТакого файла не существует")

    size = os.path.getsize(path1)   # формируем заголовки

    headers = (
        f"Keep-Alive=1\r\n"
        f"Status=200\r\n"
        f"Content-Length={size}\r\n"
        f"Content-Type=file\r\n"
        f"Path={path2}\r\n"
        f"\r\n"
    ).encode()

    socket.sendall(headers)     # отправляем заголовки

    with open(path1, "rb") as f:    # отправляем файл
        socket.sendfile(f)

    return f"Файл {path1} успешно загружен в {path2}"

def download_file(path1: str, path2: str, socket: socket.socket):
    """Эта функция реализует загрузку файла с сервера.
    Args:
        path1: путь, по которому скачается файл клиенту
        path2: путь файла, который клиент скачивает с сервера
        socket: сокет, по которому происходит общение
    Возвращает небольшое сообщение если все хорошо или вызывает ошибку."""

    headers = (     # формируем заголовки
        f"Keep-Alive=1\r\n"
        f"Status=200\r\n"
        f"Content-Length=0\r\n"
        f"Content-Type=text\r\n"
        f"Path={path2}\r\n"
        f"\r\n"
    ).encode()

    socket.sendall(headers)     # отправляем заголовки

    buffer = b""

    while b"\r\n\r\n" not in buffer:    # читаем заголовки
        chunk = socket.recv(1024)
        if not chunk:
            return f"Что-то пошло не так"
        buffer += chunk

    headers_raw, body = buffer.split(b"\r\n\r\n", 1)

    headers = {}    # кладем в словарь
    for line in headers_raw.split(b"\r\n"):
        key, value = line.split(b"=", 1)
        headers[key.decode()] = value.decode()

    content_length = int(headers.get("Content-Length", "0"))

    with open(path1, "wb") as f:    # перегоняем байты в файл
        f.write(body[:content_length])

        while f.tell() < content_length:
            remaining = content_length - f.tell()
            chunk = socket.recv(min(1024 * 1024, remaining))
            if not chunk:
                f.seek()
                f.truncate()
                return f"\nЧто-то пошло не так"
            f.write(chunk)

    return f"\nФайл {path2} успешно загружен в {path1}"


def ls_directory(path: str, socket: socket.socket):
    """Эта функция реализует запрос и отображение содержимого директории на сервере.
    Args:
        path: путь к директории на сервере
        socket: сокет, по которому происходит общение
        """

    headers = (
        f"Keep-Alive=1\r\n"
        f"Status=200\r\n"
        f"Content-Length=0\r\n"
        f"Content-Type=text\r\n"
        f"Path={path}\r\n"
        f"\r\n"
    ).encode()

    socket.sendall(headers)

    buffer = b""

    while b"\r\n\r\n" not in buffer:
        chunk = socket.recv(1024)
        if not chunk:
            return None
        buffer += chunk

    headers_raw, body = buffer.split(b"\r\n\r\n", 1)

    headers = {}
    for line in headers_raw.split(b"\r\n"):
        key, value = line.split(b"=", 1)
        headers[key.decode()] = value.decode()

    content_length = int(headers.get("Content-Length", "0"))

    size = len(body) - content_length

    print(body[:content_length].decode("utf-8"), end="")    # если нет такой директории, то сервер
                                                            # пришлет об этом сообщение и здесь оно отпринтуется
    print(socket.recv(size).decode("utf-8"))    # содержимое директории не особо большое, поэтому можно так сделать

    return ""


def manager(task: str, socket: socket.socket):
    """Здесь вызываются все функции и происходят некоторые взаимодействия с пользователем
    Args:
        task: задача-ввод от пользователя, которая здесь обрабатывается
        socket: сокет, который нужно передавать функциям"""

    status = ""

    if task == "1":
        path_of_file = input("\nВведите путь загружаемого файла: ")
        path_to_download = input("\nВведитe путь куда загрузить файл: ")
        status = upload_file(path_of_file, path_to_download, socket)

    elif task == "2":
        path_to_download = input("\nВведите путь куда скачать файл: ")
        path_of_file = input("\nВведите путь загружаемого файла: ")
        status = download_file(path_to_download, path_of_file, socket)

    elif task == "3":
        path = input("\nВведите путь: ")
        status = ls_directory(path, socket)

    elif task == "4":
        raise Exit("\nВы вышли из приложения")

    else:
        raise ValueError(f"\nВведите число от 1 до 4. Вы ввели: {task}")

    return status
    

def main():
    """Здесь настраивается сокет, обрабатываются ошибки и происходит выбор задачи"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(TIMEOUT)
        try:
            s.connect((HOST, PORT))
        except (ConnectionRefusedError, TimeoutError):
            print("Сервер не отвечает. Переподключитесь")
            return

        while True:
            task = input(f"\nЧто хотите сделать?\n\n"
                        f"1. Загрузить файл{4*" "}2. Скачать файл{4*" "}"
                        f"3. Просмотреть содержимое директории{4*" "}"
                        f"4. Выйти\n: ")
            try:
                message = manager(task, s)
                print(message)

            except ValueError as e:
                print(e)

            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                print("\nСервер сбросил подключение. Переподключитесь")
                sys.exit(1)


if __name__ == "__main__":
    main()
    