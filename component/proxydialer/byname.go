package proxydialer

import (
	"context"
	"fmt"
	"net"
	"net/netip"

	C "github.com/metacubex/mihomo/constant"
	P "github.com/metacubex/mihomo/constant/provider"
)

type Tunnel interface {
	C.Tunnel
	Proxies() map[string]C.Proxy
}

type byNameProxyDialer struct {
	proxyName    string
	providerName string
	tunnel       C.Tunnel
}

type dialerPathKey struct{}
type dialerTarget struct{ provider, proxy string }

func (d byNameProxyDialer) resolve(ctx context.Context) (context.Context, C.Proxy, error) {
	t, ok := d.tunnel.(Tunnel)
	if !ok {
		return ctx, nil, fmt.Errorf("tunnel is invalid, must be proxydialer.Tunnel, but got: %T", d.tunnel)
	}
	path, _ := ctx.Value(dialerPathKey{}).([]dialerTarget)
	target := dialerTarget{d.providerName, d.proxyName}
	for _, previous := range path {
		if previous == target {
			return ctx, nil, fmt.Errorf("dialer-proxy cycle at %s", d.proxyName)
		}
	}
	if len(path) >= 32 {
		return ctx, nil, fmt.Errorf("dialer-proxy chain exceeds 32 hops")
	}
	ctx = context.WithValue(ctx, dialerPathKey{}, append(append([]dialerTarget(nil), path...), target))
	if proxy := t.Proxies()[d.proxyName]; proxy != nil {
		return ctx, proxy, nil
	}
	if d.providerName != "" {
		if source, ok := d.tunnel.(interface {
			Providers() map[string]P.ProxyProvider
		}); ok {
			if provider := source.Providers()[d.providerName]; provider != nil {
				for _, proxy := range provider.Proxies() {
					if proxy.Name() == d.proxyName {
						provider.Touch()
						return ctx, proxy, nil
					}
				}
			}
		}
	}
	return ctx, nil, fmt.Errorf("proxyName[%s] not found in globals or provider[%s]", d.proxyName, d.providerName)
}

func (d byNameProxyDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	ctx, proxy, err := d.resolve(ctx)
	if err != nil {
		return nil, err
	}
	return New(proxy, true).DialContext(ctx, network, address)
}

func (d byNameProxyDialer) ListenPacket(ctx context.Context, network, address string, rAddrPort netip.AddrPort) (net.PacketConn, error) {
	ctx, proxy, err := d.resolve(ctx)
	if err != nil {
		return nil, err
	}
	return New(proxy, true).ListenPacket(ctx, network, address, rAddrPort)
}

func NewByName(proxyName string, tunnel C.Tunnel, providerName ...string) C.Dialer {
	d := byNameProxyDialer{proxyName: proxyName, tunnel: tunnel}
	if len(providerName) > 0 {
		d.providerName = providerName[0]
	}
	return d
}
