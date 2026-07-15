#!/usr/bin/env bash
# recon-oute-server.sh — inventário SÓ-LEITURA do host oute-server (VPC OCI).
# Roda NO oute-server (o host, não o container kubo). Nada é alterado.
# NÃO imprime segredos: valores de env/.env são mascarados; só nomes/portas/topologia.
# Uso:  bash recon-oute-server.sh   (ou:  sudo bash recon-oute-server.sh  p/ ver todas as portas)
set -uo pipefail
line(){ printf '\n=== %s ===\n' "$1"; }
have(){ command -v "$1" >/dev/null 2>&1; }

line "IDENTIDADE / OS"
uname -a
[ -f /etc/os-release ] && . /etc/os-release && echo "distro: $PRETTY_NAME"
echo "uptime: $(uptime -p 2>/dev/null)"
echo "hostname: $(hostname)"

line "É OCI? (metadata Oracle — sem segredo, só região/shape)"
curl -s -m 3 -H "Authorization: Bearer Oracle" http://169.254.169.254/opc/v2/instance/ 2>/dev/null \
  | grep -oE '"(region|canonicalRegionName|shape|availabilityDomain|hostname)"[^,]*' || echo "sem metadata OCI (ou curl bloqueado)"

line "REDE — IPs"
echo "IP público (saída): $(curl -s -m 5 ifconfig.me 2>/dev/null || echo '?')"
echo "-- interfaces --"
ip -br addr 2>/dev/null || ifconfig -a 2>/dev/null | grep -E 'inet|flags'
echo "-- tailscale --"
have tailscale && tailscale ip 2>/dev/null && tailscale status 2>/dev/null | head -5 || echo "tailscale não instalado no host"

line "PORTAS ESCUTANDO (LISTEN)"
if have ss; then sudo ss -tlnp 2>/dev/null || ss -tln; else sudo netstat -tlnp 2>/dev/null || netstat -tln; fi

line "FIREWALL LOCAL (iptables/nftables/ufw)"
have ufw && sudo ufw status 2>/dev/null | head -20
have nft && { echo "-- nft ruleset (resumo) --"; sudo nft list ruleset 2>/dev/null | grep -iE 'chain|dport|accept|drop|reject' | head -40; }
sudo iptables -L INPUT -n --line-numbers 2>/dev/null | head -40 || echo "iptables: sem permissão (rode com sudo)"

line "PROXY WEB JÁ INSTALADO? (nginx / caddy / traefik / apache)"
for p in nginx caddy traefik apache2 httpd; do
  if have "$p"; then echo ">> $p PRESENTE: $($p -v 2>&1 | head -1)"; fi
done
echo "-- serviços ativos que parecem proxy --"
have systemctl && systemctl list-units --type=service --state=running 2>/dev/null | grep -iE 'nginx|caddy|traefik|apache|httpd|cloudflared' || echo "nenhum serviço proxy ativo via systemd"
echo "-- nginx: sites configurados (nomes de arquivo, não conteúdo) --"
[ -d /etc/nginx/sites-enabled ] && ls -1 /etc/nginx/sites-enabled/ 2>/dev/null
[ -d /etc/nginx/conf.d ] && ls -1 /etc/nginx/conf.d/ 2>/dev/null
echo "-- nginx: server_name e proxy_pass declarados (só as diretivas) --"
sudo grep -rhoE 'server_name[^;]*;|listen[^;]*;|proxy_pass[^;]*;' /etc/nginx/ 2>/dev/null | sort -u | head -40 || true
echo "-- caddy: Caddyfile (domínios/reverse_proxy) --"
[ -f /etc/caddy/Caddyfile ] && sudo grep -oE '^[^ ]+\.[a-z]+|reverse_proxy[^ ]*' /etc/caddy/Caddyfile 2>/dev/null | head -20

line "CLOUDFLARED / TÚNEIS JÁ EXISTENTES"
have cloudflared && { cloudflared --version; ls -1 ~/.cloudflared/ /etc/cloudflared/ 2>/dev/null; } || echo "cloudflared não instalado"

line "DOCKER / LXD — topologia de containers"
if have docker; then
  echo "-- docker containers --"; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}' 2>/dev/null
fi
if have lxc; then
  echo "-- LXD containers --"; lxc list 2>/dev/null -c ns4t
  echo "-- LXD proxy devices (port-forwards) --"
  for c in $(lxc list -c n --format csv 2>/dev/null); do
    devs=$(lxc config device show "$c" 2>/dev/null | grep -A6 'type: proxy')
    [ -n "$devs" ] && { echo ">> $c:"; echo "$devs"; }
  done
fi

line "PORTS.md (convenção de alocação de portas do host, se existir)"
for f in ~/PORTS.md /root/PORTS.md /opt/PORTS.md ./PORTS.md; do
  [ -f "$f" ] && { echo ">> $f"; cat "$f"; break; }
done

line "FIM — cole a saída no Cowork (mascare IP público se preferir)"
