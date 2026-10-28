#!/usr/bin/env bash
# Preparar una VM recién creada. Copiar a la VM y ejecutar:
#   ./preparar-vm.sh nodo     (nodo1, nodo2, nodo3)
#   ./preparar-vm.sh docker   (solo nodo1: además instala Docker para la Fase 1)
#   ./preparar-vm.sh jmeter   (vm-jmeter: Java + JMeter 5.6.3)
set -euo pipefail
[ "$(id -u)" -ne 0 ] || { echo "Ejecutar SIN sudo (el script usa sudo donde hace falta)"; exit 1; }

sudo apt-get update && sudo apt-get upgrade -y
sudo timedatectl set-timezone America/Bogota   # para cruzar tiempos con Prometheus

case "${1:-nodo}" in
  docker)
    curl -fsSL https://get.docker.com | sh
    # Almacenamiento clásico overlay2: cAdvisor no reconoce los contenedores con el
    # almacén de imágenes de containerd (por defecto desde Docker 29)
    echo '{"features": {"containerd-snapshotter": false}}' | sudo tee /etc/docker/daemon.json >/dev/null
    sudo systemctl restart docker
    sudo usermod -aG docker "$USER"
    echo ">> Cierre sesión SSH y vuelva a entrar para usar docker sin sudo"
    ;;
  jmeter)
    sudo apt-get install -y openjdk-17-jre-headless sysstat
    cd ~
    wget -q https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz
    tar xzf apache-jmeter-5.6.3.tgz
    grep -q apache-jmeter ~/.bashrc || echo 'export PATH=$PATH:~/apache-jmeter-5.6.3/bin' >> ~/.bashrc
    echo ">> Ejecute: source ~/.bashrc && jmeter --version"
    ;;
  nodo) ;;
esac
