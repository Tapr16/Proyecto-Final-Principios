"""Modelos Pydantic (entrada y salida de la API)."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str
    categoria: str
    precio: Decimal
    stock: int


class PaginaProductos(BaseModel):
    page: int
    page_size: int
    items: list[ProductoOut]


class ResultadoBusqueda(BaseModel):
    q: str
    total: int
    items: list[ProductoOut]


class ItemIn(BaseModel):
    producto_id: int = Field(gt=0)
    cantidad: int = Field(gt=0, le=100)


class PedidoIn(BaseModel):
    cliente_id: int = Field(gt=0)
    items: list[ItemIn] = Field(min_length=1, max_length=20)


class PedidoOut(BaseModel):
    id: int
    cliente_id: int
    fecha: datetime
    total: Decimal
    num_items: int


class VentasCategoria(BaseModel):
    categoria: str
    lineas: int
    unidades: int
    total: Decimal
