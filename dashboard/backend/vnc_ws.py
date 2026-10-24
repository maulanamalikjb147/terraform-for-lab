"""
Pure Python 3 WebSocket-to-TCP VNC Proxy (Websockify)
Bridges browser WebSocket connections to QEMU/KVM VNC TCP sockets.
Zero external dependencies.
"""
import socket
import select
import struct
import base64
import hashlib
import sys
import time

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def encode_ws_frame(payload: bytes, opcode: int = 0x2) -> bytes:
    """Encode raw bytes into RFC 6455 unmasked WebSocket frame (server to client)"""
    length = len(payload)
    hdr = bytearray([0x80 | (opcode & 0x0F)])
    if length <= 125:
        hdr.append(length)
    elif length <= 65535:
        hdr.append(126)
        hdr.extend(struct.pack("!H", length))
    else:
        hdr.append(127)
        hdr.extend(struct.pack("!Q", length))
    return bytes(hdr) + payload

def recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """Read exact number of bytes from socket"""
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = sock.recv(num_bytes - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

def decode_ws_frame(sock: socket.socket):
    """Read and decode an RFC 6455 masked WebSocket frame from client"""
    head = recv_exact(sock, 2)
    if not head:
        return None, None
    b1, b2 = head[0], head[1]
    opcode = b1 & 0x0F
    is_masked = (b2 & 0x80) != 0
    payload_len = b2 & 0x7F

    if payload_len == 126:
        ext = recv_exact(sock, 2)
        if not ext:
            return None, None
        payload_len = struct.unpack("!H", ext)[0]
    elif payload_len == 127:
        ext = recv_exact(sock, 8)
        if not ext:
            return None, None
        payload_len = struct.unpack("!Q", ext)[0]

    mask = recv_exact(sock, 4) if is_masked else None
    if is_masked and not mask:
        return None, None

    data = recv_exact(sock, payload_len)
    if data is None:
        return None, None

    if is_masked and mask:
        unmasked = bytearray(data)
        for i in range(len(unmasked)):
            unmasked[i] ^= mask[i % 4]
        return opcode, bytes(unmasked)

    return opcode, data

def bridge_vnc_websocket(handler, vnc_port: int, vnc_host: str = "127.0.0.1"):
    """
    Performs RFC 6455 handshake on HTTP handler and bi-directionally bridges
    WebSocket traffic to local QEMU VNC TCP port.
    """
    key = handler.headers.get("Sec-WebSocket-Key", "").strip()
    if not key:
        handler.send_error(400, "Missing Sec-WebSocket-Key")
        return

    # 1. Connect to QEMU VNC backend
    try:
        vnc_sock = socket.create_connection((vnc_host, vnc_port), timeout=5)
        vnc_sock.setblocking(False)
    except Exception as e:
        handler.send_error(502, f"Failed to connect to VNC display on port {vnc_port}: {str(e)}")
        return

    # 2. Complete WebSocket Handshake
    accept_val = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()).decode("utf-8")
    subprotocol = handler.headers.get("Sec-WebSocket-Protocol", "").strip()
    proto_header = f"Sec-WebSocket-Protocol: {subprotocol}\r\n" if subprotocol else "Sec-WebSocket-Protocol: binary\r\n"

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_val}\r\n"
        f"{proto_header}"
        "\r\n"
    )

    client_sock = handler.connection
    client_sock.sendall(response.encode("utf-8"))
    client_sock.setblocking(False)

    # 3. Bi-directional forwarding loop using select
    # We buffer partial frames for clean non-blocking streaming
    vnc_buf = bytearray()
    client_closed = False
    vnc_closed = False

    try:
        while not client_closed and not vnc_closed:
            rlist = [client_sock, vnc_sock]
            wlist = []
            if vnc_buf:
                wlist.append(vnc_sock)

            readable, writable, exceptional = select.select(rlist, wlist, rlist, 30.0)

            if exceptional:
                break

            # Handle writes to VNC
            if vnc_sock in writable and vnc_buf:
                try:
                    sent = vnc_sock.send(vnc_buf)
                    vnc_buf = vnc_buf[sent:]
                except BlockingIOError:
                    pass
                except Exception:
                    vnc_closed = True
                    break

            # Handle reads from VNC -> Client
            if vnc_sock in readable:
                try:
                    raw = vnc_sock.recv(16384)
                    if not raw:
                        vnc_closed = True
                        break
                    # Send as unmasked binary WebSocket frame
                    frame = encode_ws_frame(raw, opcode=0x2)
                    client_sock.sendall(frame)
                except BlockingIOError:
                    pass
                except Exception:
                    client_closed = True
                    break

            # Handle reads from Client -> VNC
            if client_sock in readable:
                # We need blocking read for full frame or catch blocking
                client_sock.setblocking(True)
                opcode, payload = decode_ws_frame(client_sock)
                client_sock.setblocking(False)

                if opcode is None or opcode == 0x8:  # Connection Close
                    client_closed = True
                    # Echo close frame
                    try:
                        client_sock.sendall(encode_ws_frame(b"", opcode=0x8))
                    except Exception:
                        pass
                    break
                elif opcode == 0x9:  # Ping
                    client_sock.sendall(encode_ws_frame(payload, opcode=0xA))
                elif opcode in (0x1, 0x2):  # Binary / Text
                    if payload:
                        vnc_buf.extend(payload)
                        # Try immediate flush
                        try:
                            sent = vnc_sock.send(vnc_buf)
                            vnc_buf = vnc_buf[sent:]
                        except Exception:
                            pass

    except Exception as e:
        pass
    finally:
        try:
            vnc_sock.close()
        except Exception:
            pass
        try:
            client_sock.close()
        except Exception:
            pass
