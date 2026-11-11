package adapter

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/metacubex/http"
	"github.com/metacubex/http/httptest"
	"github.com/metacubex/mihomo/adapter/outbound"
)

func TestURLTestKeepsSuccessfulFirstMeasurement(t *testing.T) {
	previous := UnifiedDelay.Load()
	UnifiedDelay.Store(true)
	defer UnifiedDelay.Store(previous)
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			time.Sleep(20 * time.Millisecond)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		<-r.Context().Done()
	}))
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	proxy := NewProxy(outbound.NewDirect())
	delay, err := proxy.URLTest(ctx, server.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatalf("requests = %d, want 2", calls.Load())
	}
	if delay == 0 || delay >= 400 {
		t.Fatalf("delay = %d ms; failed optional request must not inflate the first measurement", delay)
	}
}

func TestURLTestReportsPositiveFastResponse(t *testing.T) {
	previous := UnifiedDelay.Load()
	UnifiedDelay.Store(false)
	defer UnifiedDelay.Store(previous)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	delay, err := NewProxy(outbound.NewDirect()).URLTest(ctx, server.URL, nil)
	if err != nil || delay == 0 {
		t.Fatalf("successful request: delay=%d err=%v", delay, err)
	}
}
