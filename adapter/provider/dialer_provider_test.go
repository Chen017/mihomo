package provider

import "testing"

func TestProviderDialerCycles(t *testing.T) {
	for _, tc := range []struct {
		name     string
		mappings []map[string]any
		bad      bool
	}{
		{"same provider", []map[string]any{{"name": "entry"}, {"name": "split", "dialer-proxy": "entry", "xhttp-opts": map[string]any{"download-settings": map[string]any{"dialer-proxy": ""}}}}, false},
		{"external group", []map[string]any{{"name": "entry", "dialer-proxy": "external"}}, false},
		{"self", []map[string]any{{"name": "entry", "dialer-proxy": "entry"}}, true},
		{"download cycle", []map[string]any{{"name": "entry", "dialer-proxy": "split"}, {"name": "split", "xhttp-opts": map[string]any{"download-settings": map[string]any{"dialer-proxy": "entry"}}}}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := validateProviderDialers(tc.mappings); (err != nil) != tc.bad {
				t.Fatalf("error=%v want failure=%v", err, tc.bad)
			}
		})
	}
}
