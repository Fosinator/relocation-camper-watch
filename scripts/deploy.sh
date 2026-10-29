#!/bin/sh
# Deploy Relocation Camper Watch from GitHub (never the local folder) to http://10.1.1.200.
#   sh scripts/deploy.sh <branch|tag|commit>
# Steps: check the ref is on GitHub -> back up the database -> stage app.new -> compile check -> swap (old code kept
# as app.prev) -> seed config.yaml if there is none yet -> restart -> health check -> run the tests inside the
# container (against relocation_watch_test) -> record in data/deploys.log.
# Back out: sh scripts/deploy.sh <previous ref>
set -e
REF=${1:?usage: deploy.sh <branch|tag|commit>}
C=relocation-camper-watch
IP=10.1.1.200
S="ssh -i $HOME/.ssh/unraid_key -o IdentitiesOnly=yes root@10.1.1.2"
A=/mnt/user/appdata/$C

git fetch -q --tags --force origin
if git rev-parse -q --verify "refs/remotes/origin/$REF" >/dev/null; then
  SHA=$(git rev-parse "refs/remotes/origin/$REF")
elif git rev-parse -q --verify "refs/tags/$REF" >/dev/null; then
  SHA=$(git rev-parse "refs/tags/$REF^{commit}")
else
  SHA=$(git rev-parse -q --verify "$REF^{commit}") || { echo "unknown ref: $REF"; exit 1; }
  [ -n "$(git branch -r --contains "$SHA")" ] || { echo "$REF is not on GitHub - push it first"; exit 1; }
fi
VERSION="$(git describe --tags --always "$SHA") ($(git rev-parse --short "$SHA"), $(git log -1 --format=%cs "$SHA"))"
STAMP=$(date +%Y%m%d-%H%M)
echo "deploying $VERSION to $C"

$S "mkdir -p $A/data/backups $A/config && docker exec exp-v2-db pg_dump -U radar -Fc relocation_watch > $A/data/backups/relocation_watch-pre-deploy-$STAMP.dump && cd $A/data/backups && ls -t | tail -n +11 | xargs -r rm --"
git archive "$SHA" app | $S "rm -rf $A/app.new && mkdir $A/app.new && tar xf - -C $A/app.new --strip-components=1 && echo '$VERSION' > $A/app.new/VERSION"
git archive "$SHA" config/config.yaml | $S "[ -f $A/config/config.yaml ] && echo 'config.yaml kept (edit it on the server)' || { tar xf - -C $A && echo 'config.yaml seeded'; }"
$S "docker run --rm -v $A/app.new:/app:ro -w /app python:3.12-slim python -c \"import glob; [compile(open(f,encoding='utf-8').read(),f,'exec') for f in glob.glob('rcw/**/*.py', recursive=True) + glob.glob('tests/*.py')]\"" \
  || { echo "compile check failed - nothing changed on $C"; exit 1; }
$S "rm -rf $A/app.prev && { [ ! -d $A/app ] || mv $A/app $A/app.prev; } && mv $A/app.new $A/app"
$S "docker restart $C >/dev/null"
for i in $(seq 1 40); do
  BODY=$(curl -s --max-time 5 "http://$IP/health" || true)
  case "$BODY" in *'"ok":true'*) break ;; esac
  sleep 3
done
case "$BODY" in *'"ok":true'*) OK=ok ;; *) OK="FAILED ($BODY)" ;; esac
$S "docker logs --since 3m $C 2>&1 | grep -iE 'error|traceback|scheduler started' | tail -8" || true
$S "echo '$(date '+%Y-%m-%d %H:%M') $VERSION $OK' >> $A/data/deploys.log"
echo "$C: $VERSION - $OK"
[ "$OK" = ok ] || { echo "Back out: sh scripts/deploy.sh <previous ref> (previous code in $A/app.prev)"; exit 1; }
echo "running tests on $C"
$S "docker exec $C sh -c '/venv/bin/python -c \"import pytest\" 2>/dev/null || /venv/bin/pip install -q --disable-pip-version-check -r /app/requirements-dev.txt' && docker exec -w /app $C /venv/bin/python -m pytest -q -p no:cacheprovider tests" \
  || { echo "TESTS FAILED on $VERSION"; exit 1; }
