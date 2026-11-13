# rtlsw.qcow2: one simulated switch as a small VM

`make vm` (`tools/vm/mkvm.sh [FIRMWARE.bin]`) builds `build/vm/rtlsw.qcow2`:
Alpine 3.21 (~54 MB), booting in a few seconds into an RTL837x switch. That's
rtlsim running the RTLPlayground firmware, as the OpenFlow controller of an
OVS bridge (kernel datapath) over the VM's NICs. It also builds
`build/vm/rtlsw-data.qcow2`, an empty data disk. The build runs in an alpine
container (Docker) and needs no root.

- **Ports:** NIC *n* is front port *n*+1, in the board's front-port order
  (the SWTG024AS: 1-4 RJ45, 5-6 SFP). Cable them with any QEMU netdev: a
  `stream` or `dgram` socket to another VM or test, a tap, and so on. A
  switch with *k* NICs has *k* cabled ports.
- **Consoles:** the first serial port is a root shell on the VM (for
  debugging). The second is the switch's own serial console, the firmware's
  CLI, as on the real switch.
- **Settings:** `-fw_cfg name=opt/rtlsw/conf,string="SFP1=10g"` (or
  `,file=rtlsw.conf`): shell variables `FIRMWARE`, `BOARD`, `SFP1`/`SFP2`
  (`10g`|`1g`: a module in that cage, with light), and `RTLSIM_ARGS`.
  `-fw_cfg name=opt/rtlsw/firmware,file=fw.bin` replaces the built-in
  firmware (the newest SWTG024AS compact build when the image was made).
- **Persistence:** without a data disk, the switch's flash is fresh at every
  boot. With one (a copy of `rtlsw-data.qcow2`, or any filesystem labelled
  `RTLSW`), its `flash.bin` keeps the saved config and firmware updates
  across boots. rtlsim writes flash changes back within 50 ms, so even a
  killed VM keeps them. `rtlsw.conf` and `firmware.bin` on that disk work as
  the `-fw_cfg` ones do.
- **The switch's address** is the firmware's (192.168.2.2 unless its config
  says otherwise), reachable through any front port, as on silicon.

```
cp build/vm/rtlsw-data.qcow2 sw1-data.qcow2
qemu-system-x86_64 -enable-kvm -m 256 -nographic \
    -drive file=build/vm/rtlsw.qcow2,if=virtio,snapshot=on -drive file=sw1-data.qcow2,if=virtio \
    -serial mon:stdio -serial unix:/tmp/sw1-console.sock,server=on,wait=off \
    -fw_cfg name=opt/rtlsw/conf,string="SFP1=10g" \
    -netdev stream,id=p1,server=on,addr.type=unix,addr.path=/tmp/sw1-p1.sock -device virtio-net-pci,netdev=p1 \
    -netdev stream,id=p2,server=off,addr.type=unix,addr.path=/tmp/vm2-eth0.sock -device virtio-net-pci,netdev=p2
```

`make test-vm` (`tests/vm/test_vm.py`) boots it twice, in about 15 s. It
needs no root, so it suits CI. Its test is the other end of two stream
sockets: the firmware console answers, a `-fw_cfg` module is found, ARP is
answered on port 1, broadcasts flood to port 2, unicast is switched, and the
data disk keeps the flash.
