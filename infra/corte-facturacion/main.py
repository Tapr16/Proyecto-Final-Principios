"""Apagado automático: desactiva la facturación del proyecto si el gasto real supera el presupuesto.

Se activa con cada aviso del presupuesto "PF experimento - gasto real" (tema Pub/Sub presupuesto-pf).
El presupuesto descuenta los créditos, así que costAmount es lo que se pagaría con la tarjeta.
Al quitar la facturación, Google detiene las VMs del proyecto. Para reactivarla:
    gcloud billing projects link <PROYECTO> --billing-account=<CUENTA>

Mensaje de prueba (no corta nada, solo verifica permisos):
    gcloud pubsub topics publish presupuesto-pf --message='{"simulacro": true}'
"""
import base64
import json
import os

import functions_framework
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import billing_v1

PROYECTO = os.environ["PROYECTO"]
NOMBRE = f"projects/{PROYECTO}"
PERMISO = "resourcemanager.projects.deleteBillingAssignment"


def tiene_permiso_de_corte():
    credenciales, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"])
    r = AuthorizedSession(credenciales).post(
        f"https://cloudresourcemanager.googleapis.com/v3/{NOMBRE}:testIamPermissions",
        json={"permissions": [PERMISO]}, timeout=30)
    r.raise_for_status()
    return PERMISO in r.json().get("permissions", [])


@functions_framework.cloud_event
def cortar_facturacion(evento):
    datos = json.loads(base64.b64decode(evento.data["message"]["data"]).decode())
    cliente = billing_v1.CloudBillingClient()
    info = cliente.get_project_billing_info(name=NOMBRE)

    if datos.get("simulacro"):
        print(f"SIMULACRO: facturación activa={info.billing_enabled}, "
              f"permiso de corte={tiene_permiso_de_corte()}")
        return

    costo, presupuesto = datos["costAmount"], datos["budgetAmount"]
    if costo <= presupuesto:
        print(f"Sin acción: gasto real {costo} <= presupuesto {presupuesto}")
        return
    if not info.billing_enabled:
        print("La facturación ya estaba desactivada")
        return

    cliente.update_project_billing_info(
        name=NOMBRE, project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name=""))
    print(f"FACTURACIÓN DESACTIVADA: gasto real {costo} > presupuesto {presupuesto}")
