"""Cancel a WebSocket without clearing WebSocketApp.sock during on_open."""


def request_disconnect(connection):
    if connection is None:
        return
    connection.keep_running = False
    # WebSocketApp.close() clears .sock while run_forever can still be using
    # it after its on_open callback. Let run_forever own that final cleanup.
    transport = getattr(connection, 'sock', None)
    if transport is not None:
        try:
            transport.shutdown()
        except Exception:
            pass
