import json
import os

import aio_pika
from dotenv import load_dotenv

from . import simulador

load_dotenv()

RABBITMQ_URL = os.getenv("RABBITMQ_URL")
EXCHANGE_NAME = "logitrack_events"

_HANDLERS = {
    "route.assigned": simulador.procesar_route_assigned,
    "shipment.returned": simulador.procesar_envio_devuelto,
    "shipment.delivered": simulador.procesar_entrega_manual,
    "customs.held": simulador.procesar_aduana_retiene,
    "customs.cleared": simulador.procesar_aduana_libera,
}


async def _on_message(message: aio_pika.IncomingMessage):
    async with message.process():
        payload = json.loads(message.body.decode())
        handler = _HANDLERS.get(message.routing_key)
        if handler:
            try:
                await handler(payload)
            except Exception as exc:
                print(f"[tracking] error procesando {message.routing_key}: {exc}")


async def iniciar_consumidor():
    connection = await aio_pika.connect_robust(RABBITMQ_URL)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )
    queue = await channel.declare_queue("tracking_ingestion_service.eventos", durable=True)
    for clave in _HANDLERS:
        await queue.bind(exchange, routing_key=clave)
    await queue.consume(_on_message)
    return connection