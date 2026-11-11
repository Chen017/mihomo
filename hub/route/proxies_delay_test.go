package route

import (
	"context"
	"testing"

	"github.com/metacubex/http"
	"github.com/metacubex/http/httptest"
	"github.com/metacubex/mihomo/common/utils"
	C "github.com/metacubex/mihomo/constant"
)

type fallbackDelayProxy struct {
	C.Proxy
	measured bool
}

func (p fallbackDelayProxy) URLTest(ctx context.Context, _ string, _ utils.IntRanges[uint16]) (uint16, error) {
	<-ctx.Done()
	if p.measured {
		return 25, nil
	}
	return 0, ctx.Err()
}

func TestProxyDelayPreservesMeasuredFallback(t *testing.T) {
	for _, measured := range []bool{true, false} {
		req := httptest.NewRequest(http.MethodGet, "/?timeout=10&url=https%3A%2F%2Fexample.test", nil)
		req = req.WithContext(context.WithValue(req.Context(), CtxKeyProxy, fallbackDelayProxy{measured: measured}))
		response := httptest.NewRecorder()
		getProxyDelay(response, req)
		want := http.StatusGatewayTimeout
		if measured {
			want = http.StatusOK
		}
		if response.Code != want {
			t.Fatalf("measured=%v: status=%d, want %d", measured, response.Code, want)
		}
	}
}
