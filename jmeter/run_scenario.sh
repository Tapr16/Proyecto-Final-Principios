#!/usr/bin/env bash
# Ejecuta un escenario completo: calentamiento + 4 niveles de carga × 3 repeticiones.
# Uso:   ./run_scenario.sh <escenario> <host> <puerto>
# Ej.:   ./run_scenario.sh docker    10.0.1.4 8000
#        ./run_scenario.sh k8s-2n-3r 10.0.1.4 30080
# Variables opcionales: LEVELS="10 50 100 200 400" REPS=3 DUR=180 RAMP=30
#
# ANTES de correrlo: resetear la BD (seed) y verificar que todo esté Running/Ready.
set -euo pipefail

ESC=${1:?Falta el escenario}; HOST=${2:?Falta el host}; PORT=${3:?Falta el puerto}
LEVELS=${LEVELS:-"10 50 100 200"}; REPS=${REPS:-3}; DUR=${DUR:-180}; RAMP=${RAMP:-30}

DIR=$(cd "$(dirname "$0")" && pwd)
RESULTS=$DIR/../results
OUT=$RESULTS/$ESC
mkdir -p "$OUT"
[ -f "$RESULTS/runs.csv" ] || echo "escenario,usuarios,rep,inicio,fin" > "$RESULTS/runs.csv"

JMETER_OPTS=(-n -t "$DIR/plan.jmx" -Jhost="$HOST" -Jport="$PORT" -Jcsv="$DIR/ids.csv"
             -Jjmeter.save.saveservice.output_format=csv -j "$DIR/jmeter.log")

echo ">> Verificando que la API responde en http://$HOST:$PORT/health"
curl -fsS "http://$HOST:$PORT/health" > /dev/null

echo ">> Calentamiento (60 s, se descarta)"
jmeter "${JMETER_OPTS[@]}" -Jthreads=20 -Jrampup=10 -Jduration=60 -l "$OUT/warmup.jtl" > /dev/null
rm -f "$OUT/warmup.jtl"
sleep 30

for T in $LEVELS; do
  for R in $(seq 1 "$REPS"); do
    echo ">> $ESC | $T usuarios | repetición $R | $(date '+%F %T')"
    # CPU de vm-jmeter durante la corrida (si pasa de 80 %, el cuello es JMeter)
    vmstat 5 > "$OUT/u${T}_r${R}_vmstat.txt" & VMSTAT=$!
    START=$(date +%s)
    jmeter "${JMETER_OPTS[@]}" -Jthreads="$T" -Jrampup="$RAMP" -Jduration="$DUR" \
      -l "$OUT/u${T}_r${R}.jtl" > /dev/null
    END=$(date +%s)
    kill $VMSTAT 2>/dev/null || true
    echo "$ESC,$T,$R,$START,$END" >> "$RESULTS/runs.csv"   # para cruzar con Prometheus
    sleep 30   # enfriamiento entre corridas
  done
done
echo ">> Escenario $ESC terminado. Resultados en $OUT"
