import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AddressOptions } from "./ProxyAddress";

describe("device connection address options", () => {
  it("shows both physical interfaces with the running port", () => {
    const html = renderToStaticMarkup(
      <AddressOptions
        state={{
          addresses: [
            {
              interface: "en9",
              label: "USB Ethernet",
              host: "10.110.10.15",
              kind: "ethernet",
            },
            {
              interface: "en0",
              label: "Wi-Fi",
              host: "192.168.111.180",
              kind: "wifi",
            },
          ],
          selected_interface: null,
          active_interface: "en0",
          host: "192.168.111.180",
          warning: null,
        }}
        port={9999}
      />,
    );
    expect(html).toContain("Wi-Fi · 192.168.111.180:9999");
    expect(html).toContain("USB Ethernet · 10.110.10.15:9999");
  });

  it("retains a disconnected explicit selection as an unavailable option", () => {
    const html = renderToStaticMarkup(
      <AddressOptions
        state={{
          addresses: [],
          selected_interface: "en9",
          active_interface: null,
          host: null,
          warning: "网卡已断开",
        }}
        port={8888}
      />,
    );
    expect(html).toContain('value="en9"');
    expect(html).toContain("en9（已断开）");
  });
});
