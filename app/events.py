import asyncio
import json
import os

import aio_pika
from dotenv import load_dotenv

load_dotenv()

RABBITMQ_URL = os.getenv("RABBITMQ_URL")
EXCHANGE_NAME = "logitrack_events"

_conexion = None
_exchange = None
_candado = asyncio.Lock()


async def _exchange_listo():
    global _conexion, _exchange
    async with _candado:
        if _conexion is None or _conexion.is_closed:
            _conexion = await aio_pika.connect_robust(RABBITMQ_URL)
            canal = await _conexion.channel()
            _exchange = await canal.declare_exchange(
                EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
            )
        return _exchange


async def publish_event(routing_key: str, payload: dict):
    exchange = await _exchange_listo()
    message = aio_pika.Message(
        body=json.dumps(payload, default=str).encode(),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await exchange.publish(message, routing_key=routing_key)


async def cerrar_publicador():
    global _conexion, _exchange
    if _conexion is not None and not _conexion.is_closed:
        await _conexion.close()
    _conexion = None
    _exchange = None