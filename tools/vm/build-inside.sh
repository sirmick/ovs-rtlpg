#!/bin/sh
# build-inside.sh - runs in an alpine container (tools/vm/mkvm.sh): builds
# rtlsim against musl, an Alpine root with OVS and the kernel, and the disk:
# MBR + a FAT boot partition (syslinux, kernel, initramfs) + an ext4 root,
# all without mounting anything (mkfs.ext4 -d, mtools), so no privileges.
#   /src   the ovs-rtlpg tree (read-only)    /rtlpg  RTLPlayground (headers)
#   /fw    firmware.bin to build in           /out    where rtlsw.qcow2 goes
set -eu
ALPINE=v3.21
apk add -q build-base linux-headers python3 syslinux mtools dosfstools e2fsprogs sfdisk qemu-img mkinitfs >/dev/null

# rtlsim, for musl
cp -a /src /work
make -C /work -j"$(nproc)" BUILD=build-musl RTLPG=/rtlpg build-musl/rtlsim >/dev/null
strip /work/build-musl/rtlsim

# the root
R=/root-fs
mkdir -p $R/etc/apk
cp -a /etc/apk/keys $R/etc/apk/
printf '%s\n' "https://dl-cdn.alpinelinux.org/alpine/$ALPINE/main" \
	"https://dl-cdn.alpinelinux.org/alpine/$ALPINE/community" > $R/etc/apk/repositories
apk add -q --root $R --initdb --no-cache alpine-base openrc busybox-openrc linux-virt openvswitch jq libstdc++ \
	e2fsprogs >/dev/null
cp -a /src/tools/vm/rootfs/. $R/
install -D -m 755 /work/build-musl/rtlsim $R/usr/bin/rtlsim
install -D -m 644 /fw/firmware.bin $R/usr/share/rtlsw/firmware.bin
mkdir -p $R/usr/share/rtlsw/boards
cp /src/boards/*.json $R/usr/share/rtlsw/boards/
python3 /src/tools/mksfp.py $R/usr/share/rtlsw/sfp-10g.bin --speed 10g
python3 /src/tools/mksfp.py $R/usr/share/rtlsw/sfp-1g.bin --speed 1g --model SFP-1G-SX
echo rtlsw > $R/etc/hostname
printf '/dev/vda2\t/\text4\tdefaults,noatime\t0 1\n' > $R/etc/fstab
# console: a root shell on ttyS0 (the firmware has ttyS1)
cat > $R/etc/inittab <<'EOT'
::sysinit:/sbin/openrc sysinit
::sysinit:/sbin/openrc boot
::wait:/sbin/openrc default
ttyS0::respawn:/sbin/getty -n -l /bin/sh -L 115200 ttyS0 vt100
::ctrlaltdel:/sbin/reboot
::shutdown:/sbin/openrc shutdown
EOT
for s in devfs dmesg mdev hwdrivers; do ln -sf /etc/init.d/$s $R/etc/runlevels/sysinit/$s; done
for s in modules hostname bootmisc sysctl localmount; do ln -sf /etc/init.d/$s $R/etc/runlevels/boot/$s; done
ln -sf /etc/init.d/rtlsw $R/etc/runlevels/default/rtlsw
for s in killprocs mount-ro; do ln -sf /etc/init.d/$s $R/etc/runlevels/shutdown/$s; done
KVER=$(ls $R/lib/modules)
mkinitfs -b $R -F "base virtio ext4" -o $R/boot/initramfs-virt "$KVER" >/dev/null

# the disk: 1 MiB MBR gap, 32 MiB FAT /boot, the rest ext4 /
W=/disk
mkdir -p $W
dd if=/dev/zero of=$W/boot.img bs=1M count=32 status=none
mkfs.vfat -n BOOT $W/boot.img >/dev/null
cat > $W/syslinux.cfg <<'EOT'
DEFAULT rtlsw
PROMPT 0
TIMEOUT 0
LABEL rtlsw
	LINUX /vmlinuz-virt
	INITRD /initramfs-virt
	APPEND root=/dev/vda2 rootfstype=ext4 modules=ext4,virtio_blk console=ttyS0,115200 quiet
EOT
mcopy -i $W/boot.img $R/boot/vmlinuz-virt $R/boot/initramfs-virt $W/syslinux.cfg /usr/share/syslinux/ldlinux.c32 ::/
syslinux --install $W/boot.img
rm -rf $R/boot/*
ROOT_MB=$(( $(du -sm $R | cut -f1) * 13 / 10 + 32 ))
mkfs.ext4 -q -L rtlswroot -d $R $W/root.img ${ROOT_MB}M
TOTAL=$(( 1 + 32 + ROOT_MB ))
dd if=/dev/zero of=$W/disk.raw bs=1M count=$TOTAL status=none
printf 'label: dos\nstart=2048, size=65536, type=c, bootable\nstart=67584, type=83\n' | sfdisk -q $W/disk.raw
dd if=/usr/share/syslinux/mbr.bin of=$W/disk.raw conv=notrunc bs=440 count=1 status=none
dd if=$W/boot.img of=$W/disk.raw conv=notrunc bs=1M seek=1 status=none
dd if=$W/root.img of=$W/disk.raw conv=notrunc bs=1M seek=33 status=none
qemu-img convert -c -O qcow2 $W/disk.raw /out/rtlsw.qcow2
# an empty data disk: copy it per switch to keep its flash (and config) across boots
mkfs.ext4 -q -L RTLSW $W/data.raw 16M
qemu-img convert -O qcow2 $W/data.raw /out/rtlsw-data.qcow2
chown "$(stat -c %u:%g /out)" /out/rtlsw.qcow2 /out/rtlsw-data.qcow2	# the caller's, not the container's root
echo "rtlsw.qcow2: $(du -h /out/rtlsw.qcow2 | cut -f1), root ${ROOT_MB} MiB, kernel $KVER"
