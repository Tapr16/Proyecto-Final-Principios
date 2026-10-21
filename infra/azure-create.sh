#!/usr/bin/env bash
# Gestión de las VMs del proyecto en Azure.
# Uso: ./azure-create.sh {crear|ips|apagar|encender|borrar}
set -euo pipefail

RG=rg-pf
LOC=${LOC:-northcentralus}   # Azure for Students: única región permitida con D2as_v5
SIZE=Standard_D2as_v5        # NO usar serie B (burstable)
VMS="nodo1 nodo2 nodo3 vm-jmeter"

case "${1:-}" in
  crear)
    az group create -n $RG -l $LOC
    az network vnet create -g $RG -n vnet-pf \
      --address-prefix 10.0.0.0/16 --subnet-name snet-pf --subnet-prefix 10.0.1.0/24
    for VM in $VMS; do
      az vm create -g $RG -n $VM \
        --image Ubuntu2404 --size $SIZE \
        --vnet-name vnet-pf --subnet snet-pf \
        --admin-username azureuser --generate-ssh-keys \
        --public-ip-sku Standard --os-disk-size-gb 64
    done
    az vm list-ip-addresses -g $RG -o table
    ;;
  ips)
    az vm list-ip-addresses -g $RG -o table
    ;;
  apagar)      # deallocate: deja de cobrar CPU (apagar desde Ubuntu NO basta)
    for VM in $VMS; do az vm deallocate -g $RG -n $VM --no-wait; done
    ;;
  encender)
    for VM in $VMS; do az vm start -g $RG -n $VM --no-wait; done
    ;;
  borrar)      # SOLO al final, después de copiar results/
    az group delete -n $RG --yes
    ;;
  *)
    echo "Uso: $0 {crear|ips|apagar|encender|borrar}"; exit 1 ;;
esac
