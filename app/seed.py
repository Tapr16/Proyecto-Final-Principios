"""Genera (o regenera) los datos de la tienda.

Uso:
    python seed.py                          # crea tablas, trunca y carga todo
    python seed.py --escala 0.05            # 5 % del volumen (pruebas locales)
    python seed.py --solo-csv ../jmeter/ids.csv   # solo genera ids.csv, sin BD

Los datos son deterministas (semilla fija): cada vez que se corre se obtiene
exactamente la misma base de datos, así todas las fases parten de lo mismo.
Volúmenes con escala 1: 100.000 productos, 20.000 clientes, 200.000 pedidos
y ~600.000 items.
"""
import argparse
import asyncio
import csv
import random
import time
from datetime import datetime, timedelta
from decimal import Decimal

import asyncpg
from faker import Faker

from db import DATABASE_URL, engine
from models import Base

SEMILLA = 42
LOTE = 5_000

N_PRODUCTOS = 100_000
N_CLIENTES = 20_000
N_PEDIDOS = 200_000
N_FILAS_CSV = 5_000

CATEGORIAS = [
    "electronica", "hogar", "deportes", "ropa", "juguetes", "libros",
    "belleza", "jardin", "mascotas", "oficina", "automotriz", "alimentos",
]
SUSTANTIVOS = [
    "lampara", "silla", "mesa", "camiseta", "balon", "audifonos", "teclado",
    "mouse", "cuaderno", "mochila", "botella", "reloj", "zapatos", "chaqueta",
    "cafetera", "licuadora", "parlante", "cargador", "cable", "maceta",
    "collar", "juguete", "perfume", "crema", "llanta", "aceite", "galletas",
    "taladro", "almohada", "toalla",
]
ADJETIVOS = [
    "basico", "premium", "compacto", "portatil", "clasico", "moderno",
    "deportivo", "ecologico", "inalambrico", "resistente", "ligero", "grande",
    "mini", "pro", "plus", "infantil", "elegante", "digital", "termico", "suave",
]
MARCAS = [
    "andina", "condor", "tayrona", "orinoco", "sierra", "caribe", "paramo",
    "macondo", "guadua", "ceiba", "nevado", "llanero", "pacifico", "amazonia",
    "cordillera",
]
# Términos de búsqueda: aparecen en los nombres con distinta frecuencia.
TERMINOS = SUSTANTIVOS + ADJETIVOS + MARCAS


def generar_productos(n, rnd, fake):
    for i in range(1, n + 1):
        nombre = f"{rnd.choice(SUSTANTIVOS)} {rnd.choice(ADJETIVOS)} {rnd.choice(MARCAS)}"
        yield (
            i,
            nombre,
            fake.sentence(nb_words=12),
            rnd.choice(CATEGORIAS),
            Decimal(rnd.randint(1_000, 2_000_000)) / 100,
            rnd.randint(0, 500),
        )


def generar_clientes(n, fake):
    for i in range(1, n + 1):
        yield (i, fake.name(), fake.email(), fake.city())


def generar_pedidos(n_pedidos, n_clientes, precios, rnd):
    """Devuelve (pedidos, items). Cada pedido tiene entre 1 y 5 items (media 3)."""
    inicio = datetime(2025, 1, 1)
    segundos_anio = 365 * 24 * 3600
    pedidos, items = [], []
    item_id = 0
    n_productos = len(precios)
    for pid in range(1, n_pedidos + 1):
        total = Decimal("0")
        for _ in range(rnd.randint(1, 5)):
            item_id += 1
            prod = rnd.randint(1, n_productos)
            cant = rnd.randint(1, 5)
            precio = precios[prod - 1]
            total += precio * cant
            items.append((item_id, pid, prod, cant, precio))
        fecha = inicio + timedelta(seconds=rnd.randrange(segundos_anio))
        pedidos.append((pid, rnd.randint(1, n_clientes), fecha, total))
    return pedidos, items


def escribir_ids_csv(ruta, n_productos, n_clientes):
    """Datos de prueba para JMeter: IDs válidos, categorías y términos."""
    rnd = random.Random(SEMILLA + 1)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["producto_id", "cliente_id", "categoria", "termino"])
        for _ in range(N_FILAS_CSV):
            w.writerow([
                rnd.randint(1, n_productos),
                rnd.randint(1, n_clientes),
                rnd.choice(CATEGORIAS),
                rnd.choice(TERMINOS),
            ])
    print(f"ids.csv escrito en {ruta} ({N_FILAS_CSV} filas)")


async def copiar(conn, tabla, columnas, filas):
    for i in range(0, len(filas), LOTE):
        await conn.copy_records_to_table(tabla, records=filas[i:i + LOTE], columns=columnas)


async def cargar(escala):
    n_prod = max(100, int(N_PRODUCTOS * escala))
    n_cli = max(100, int(N_CLIENTES * escala))
    n_ped = max(100, int(N_PEDIDOS * escala))

    t0 = time.perf_counter()
    Faker.seed(SEMILLA)
    fake = Faker("es_CO")
    rnd = random.Random(SEMILLA)

    print("Generando datos...")
    productos = list(generar_productos(n_prod, rnd, fake))
    clientes = list(generar_clientes(n_cli, fake))
    pedidos, items = generar_pedidos(n_ped, n_cli, [p[4] for p in productos], rnd)
    print(f"  {len(productos)} productos, {len(clientes)} clientes, "
          f"{len(pedidos)} pedidos, {len(items)} items "
          f"({time.perf_counter() - t0:.1f}s)")

    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await engine.dispose()

    conn = await asyncpg.connect(DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        async with conn.transaction():
            await conn.execute(
                "TRUNCATE items_pedido, pedidos, clientes, productos RESTART IDENTITY CASCADE"
            )
            print("Insertando...")
            await copiar(conn, "productos",
                         ["id", "nombre", "descripcion", "categoria", "precio", "stock"], productos)
            await copiar(conn, "clientes", ["id", "nombre", "email", "ciudad"], clientes)
            await copiar(conn, "pedidos", ["id", "cliente_id", "fecha", "total"], pedidos)
            await copiar(conn, "items_pedido",
                         ["id", "pedido_id", "producto_id", "cantidad", "precio_unitario"], items)
            # Los IDs se insertaron explícitamente: hay que mover las secuencias
            # para que los POST /pedidos generen IDs nuevos.
            for tabla in ("productos", "clientes", "pedidos", "items_pedido"):
                await conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{tabla}', 'id'), "
                    f"(SELECT MAX(id) FROM {tabla}))"
                )
        await conn.execute("ANALYZE")
    finally:
        await conn.close()
    print(f"Seed completo en {time.perf_counter() - t0:.1f}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--escala", type=float, default=1.0,
                        help="fracción del volumen de datos (1.0 = completo)")
    parser.add_argument("--solo-csv", metavar="RUTA",
                        help="solo escribe ids.csv para JMeter, sin tocar la BD")
    args = parser.parse_args()

    if args.solo_csv:
        escribir_ids_csv(args.solo_csv, int(N_PRODUCTOS * args.escala),
                         int(N_CLIENTES * args.escala))
    else:
        asyncio.run(cargar(args.escala))


if __name__ == "__main__":
    main()
