# rkn_blackhole

До первого запуска нужно создать протокол:
sudo mkdir -p /etc/iproute2/rt_protos.d
echo "200 blackhole" | sudo tee /etc/iproute2/rt_protos.d/blackhole.conf
