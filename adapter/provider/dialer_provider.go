package provider

import "fmt"

// validateProviderDialers rejects local cycles before publishing a refreshed
// provider. XHTTP owns long-lived transport contexts, so a runtime context guard
// alone cannot detect a cycle that enters a newly created HTTP transport.
func validateProviderDialers(mappings []map[string]any, globals map[string]struct{}) error {
	graph := make(map[string][]string, len(mappings))
	for _, mapping := range mappings {
		name, _ := mapping["name"].(string)
		refs := []string{}
		if ref, _ := mapping["dialer-proxy"].(string); ref != "" {
			refs = append(refs, ref)
		}
		if x, ok := mapping["xhttp-opts"].(map[string]any); ok {
			if down, ok := x["download-settings"].(map[string]any); ok {
				if ref, _ := down["dialer-proxy"].(string); ref != "" {
					refs = append(refs, ref)
				}
			}
		}
		graph[name] = refs
	}
	state := make(map[string]uint8, len(graph))
	var visit func(string) error
	visit = func(name string) error {
		if state[name] == 1 {
			return fmt.Errorf("provider proxy [%s] has circular dialer-proxy dependency", name)
		}
		if state[name] == 2 {
			return nil
		}
		state[name] = 1
		for _, ref := range graph[name] {
			if _, global := globals[ref]; global {
				continue // Match the dialer's global-before-provider lookup order.
			}
			if _, local := graph[ref]; local {
				if err := visit(ref); err != nil {
					return err
				}
			}
		}
		state[name] = 2
		return nil
	}
	for name := range graph {
		if err := visit(name); err != nil {
			return err
		}
	}
	return nil
}
