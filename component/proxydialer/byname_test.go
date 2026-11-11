package proxydialer

import (
	"context"
	C "github.com/metacubex/mihomo/constant"
	P "github.com/metacubex/mihomo/constant/provider"
	"testing"
)

type testTunnel struct {
	C.Tunnel
	globals   map[string]C.Proxy
	providers map[string]P.ProxyProvider
}

func (t testTunnel) Proxies() map[string]C.Proxy           { return t.globals }
func (t testTunnel) Providers() map[string]P.ProxyProvider { return t.providers }

type testProxy struct {
	C.Proxy
	name string
}

func (p *testProxy) Name() string { return p.name }

type testProvider struct {
	P.ProxyProvider
	proxies []C.Proxy
	touched int
}

func (p *testProvider) Proxies() []C.Proxy { return p.proxies }
func (p *testProvider) Touch()             { p.touched++ }

func TestProviderDialerScopeRefreshAndCycles(t *testing.T) {
	first := &testProxy{name: "entry"}
	second := &testProxy{name: "entry"}
	foreign := &testProxy{name: "foreign"}
	provider := &testProvider{proxies: []C.Proxy{first}}
	tunnel := testTunnel{globals: map[string]C.Proxy{}, providers: map[string]P.ProxyProvider{"mine": provider, "other": &testProvider{proxies: []C.Proxy{foreign}}}}
	d := byNameProxyDialer{proxyName: "entry", providerName: "mine", tunnel: tunnel}
	ctx, p, err := d.resolve(context.Background())
	if err != nil || p != first {
		t.Fatalf("resolve = %v, %v", p, err)
	}
	if _, _, err = d.resolve(ctx); err == nil {
		t.Fatal("cycle accepted")
	}
	provider.proxies = []C.Proxy{second}
	if _, p, err = d.resolve(context.Background()); err != nil || p != second {
		t.Fatal("provider refresh retained stale proxy")
	}
	d.proxyName = "foreign"
	if _, _, err = d.resolve(context.Background()); err == nil {
		t.Fatal("another provider was searched")
	}
	d.proxyName = "entry"
	d.providerName = ""
	if _, _, err = d.resolve(context.Background()); err == nil {
		t.Fatal("global dialer searched arbitrary providers")
	}
	tunnel.globals["entry"] = first
	d.tunnel = tunnel
	if _, p, err = d.resolve(context.Background()); err != nil || p != first {
		t.Fatal("global reference failed")
	}
}
