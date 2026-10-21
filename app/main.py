"""API REST de la tienda online (FastAPI + PostgreSQL)."""
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import func, insert, select, text

from db import AsyncSession, engine, get_session
from models import Cliente, ItemPedido, Pedido, Producto
from schemas import (
    PaginaProductos,
    PedidoIn,
    PedidoOut,
    ProductoOut,
    ResultadoBusqueda,
    VentasCategoria,
)

PAGE_SIZE = 20

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Tienda PF", version="1.0", lifespan=lifespan)
Instrumentator(excluded_handlers=["/metrics", "/health"]).instrument(app).expose(
    app, include_in_schema=False
)


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    """Sonda de vida/readiness: responde 200 solo si la BD contesta."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"status": "ok"}


# /productos/buscar debe declararse antes de /productos/{producto_id}
@app.get("/productos/buscar", response_model=ResultadoBusqueda)
async def buscar_productos(
    q: str = Query(min_length=2, max_length=50),
    session: AsyncSession = Depends(get_session),
):
    """Búsqueda ILIKE en nombre y descripción, sin índice (lectura costosa).

    Se devuelve también el total de coincidencias, lo que obliga a recorrer
    toda la tabla en cada petición.
    """
    patron = f"%{q}%"
    filtro = Producto.nombre.ilike(patron) | Producto.descripcion.ilike(patron)
    total = await session.scalar(select(func.count()).where(filtro))
    filas = await session.scalars(
        select(Producto).where(filtro).order_by(Producto.id).limit(PAGE_SIZE)
    )
    return {"q": q, "total": total, "items": filas.all()}


@app.get("/productos/{producto_id}", response_model=ProductoOut)
async def obtener_producto(
    producto_id: int, session: AsyncSession = Depends(get_session)
):
    producto = await session.get(Producto, producto_id)
    if producto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Producto no encontrado")
    return producto


@app.get("/productos", response_model=PaginaProductos)
async def listar_productos(
    categoria: str,
    page: int = Query(1, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    filas = await session.scalars(
        select(Producto)
        .where(Producto.categoria == categoria)
        .order_by(Producto.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    return {"page": page, "page_size": PAGE_SIZE, "items": filas.all()}


@app.post("/pedidos", response_model=PedidoOut, status_code=status.HTTP_201_CREATED)
async def crear_pedido(pedido: PedidoIn, session: AsyncSession = Depends(get_session)):
    """Escritura transaccional: un pedido y sus items en una sola transacción."""
    async with session.begin():
        if await session.get(Cliente, pedido.cliente_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente no encontrado")

        ids = {item.producto_id for item in pedido.items}
        precios = dict(
            (await session.execute(
                select(Producto.id, Producto.precio).where(Producto.id.in_(ids))
            )).all()
        )
        faltantes = ids - precios.keys()
        if faltantes:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Productos no encontrados: {sorted(faltantes)}"
            )

        total = sum(
            (precios[i.producto_id] * i.cantidad for i in pedido.items), Decimal("0")
        )
        fecha = datetime.now()
        pedido_id = await session.scalar(
            insert(Pedido)
            .values(cliente_id=pedido.cliente_id, fecha=fecha, total=total)
            .returning(Pedido.id)
        )
        await session.execute(
            insert(ItemPedido),
            [
                {
                    "pedido_id": pedido_id,
                    "producto_id": i.producto_id,
                    "cantidad": i.cantidad,
                    "precio_unitario": precios[i.producto_id],
                }
                for i in pedido.items
            ],
        )
    return {
        "id": pedido_id,
        "cliente_id": pedido.cliente_id,
        "fecha": fecha,
        "total": total,
        "num_items": len(pedido.items),
    }


@app.get("/reportes/ventas-por-categoria", response_model=list[VentasCategoria])
async def ventas_por_categoria(session: AsyncSession = Depends(get_session)):
    """Agregación (JOIN + GROUP BY) sobre todos los items vendidos."""
    filas = await session.execute(
        select(
            Producto.categoria,
            func.count().label("lineas"),
            func.sum(ItemPedido.cantidad).label("unidades"),
            func.sum(ItemPedido.cantidad * ItemPedido.precio_unitario).label("total"),
        )
        .join(Producto, Producto.id == ItemPedido.producto_id)
        .group_by(Producto.categoria)
        .order_by(Producto.categoria)
    )
    return [dict(f._mapping) for f in filas]
