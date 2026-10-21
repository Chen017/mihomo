package outbound

import (
	"context"
	"github.com/metacubex/mihomo/common/structure"
	C "github.com/metacubex/mihomo/constant"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

type countingDialer struct {
	calls  atomic.Int32
	target string
}

func (d *countingDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	d.calls.Add(1)
	return (&net.Dialer{}).DialContext(ctx, network, d.target)
}

// API dialers always have priority; named proxy tests cover normal user configuration.
type downloadTestProxy struct {
	C.Proxy
	dialer *countingDialer
}

func (p downloadTestProxy) DialContext(ctx context.Context, m *C.Metadata) (C.Conn, error) {
	c, e := p.dialer.DialContext(ctx, "tcp", m.RemoteAddress())
	if e != nil {
		return nil, e
	}
	return NewConn(c, &Base{}), nil
}

type downloadTestTunnel struct {
	C.Tunnel
	proxy C.Proxy
}

func (t downloadTestTunnel) Proxies() map[string]C.Proxy { return map[string]C.Proxy{"up": t.proxy} }

func TestXHTTPDownloadDialerOverride(t *testing.T) {
	for _, tc := range []struct {
		name      string
		override  *string
		wantCalls int32
	}{{"inherit", nil, 2}, {"direct", new(string), 1}} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(200)
				if f, ok := w.(http.Flusher); ok {
					f.Flush()
				}
				if r.Method == "GET" {
					_, _ = w.Write([]byte("download"))
				} else {
					_, _ = io.Copy(io.Discard, r.Body)
				}
			}))
			defer server.Close()
			host, port, _ := net.SplitHostPort(server.Listener.Addr().String())
			n, _ := strconv.Atoi(port)
			d := &countingDialer{target: server.Listener.Addr().String()}
			v, err := NewVless(VlessOption{BasicOption: BasicOption{DialerProxy: "up", TunnelForAPI: downloadTestTunnel{proxy: downloadTestProxy{dialer: d}}}, Name: "split", Server: host, Port: n, UUID: "00000000-0000-4000-8000-000000000001", Network: "xhttp", ALPN: []string{"http/1.1"}, XHTTPOpts: XHTTPOptions{Mode: "stream-up", DownloadSettings: &XHTTPDownloadSettings{DialerProxy: tc.override}}})
			if err != nil {
				t.Fatal(err)
			}
			defer v.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			conn, err := v.xhttpClient.Dial(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			if _, err = conn.Write([]byte("upload")); err != nil {
				t.Fatal(err)
			}
			buf := make([]byte, 8)
			if _, err = io.ReadFull(conn, buf); err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(time.Second)
			for d.calls.Load() < tc.wantCalls && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			if got := d.calls.Load(); got != tc.wantCalls {
				t.Fatalf("upstream dialer used %d times, want %d", got, tc.wantCalls)
			}
		})
	}
}

func TestXHTTPDownloadDialerDecode(t *testing.T) {
	for _, value := range []any{nil, "", "DIRECT", "other"} {
		fields := map[string]any{}
		if value != nil {
			fields["dialer-proxy"] = value
		}
		var option VlessOption
		if err := structure.NewDecoder(structure.Option{TagName: "proxy", WeaklyTypedInput: true}).Decode(map[string]any{"name": "split", "server": "127.0.0.1", "port": 443, "uuid": "00000000-0000-4000-8000-000000000001", "xhttp-opts": map[string]any{"download-settings": fields}}, &option); err != nil {
			t.Fatal(err)
		}
		got := option.XHTTPOpts.DownloadSettings.DialerProxy
		if value == nil && got != nil || value != nil && (got == nil || *got != value.(string)) {
			t.Fatalf("decoded %v as %v", value, got)
		}
	}
}
