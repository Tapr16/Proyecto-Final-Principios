"""Modelo de datos de la tienda online.

Índices (iguales en todas las fases):
  - productos.categoria       -> listado paginado por categoría
  - pedidos.cliente_id        -> pedidos de un cliente
  - items_pedido.pedido_id    -> items de un pedido
  - items_pedido.producto_id  -> reporte de ventas por categoría
La búsqueda por nombre (ILIKE '%texto%') NO tiene índice a propósito:
es el endpoint "costoso" del experimento.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str] = mapped_column(String(50), index=True)
    precio: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int]


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(150))
    ciudad: Mapped[str] = mapped_column(String(100))


class Pedido(Base):
    __tablename__ = "pedidos"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    fecha: Mapped[datetime]
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    items: Mapped[list["ItemPedido"]] = relationship(back_populates="pedido")


class ItemPedido(Base):
    __tablename__ = "items_pedido"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id"), index=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id"), index=True)
    cantidad: Mapped[int]
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    pedido: Mapped[Pedido] = relationship(back_populates="items")
