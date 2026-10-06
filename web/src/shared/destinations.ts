export type ToolId =
  | "texture"
  | "package"
  | "hair"
  | "object"
  | "painting"
  | "sim"
  | "upscale";
export type DestinationId = "home" | ToolId;
export type ToolGroup = "Sim appearance" | "Objects & décor" | "Image tools";
export interface Destination {
  id: DestinationId;
  route: string;
  label: string;
  panel: string;
  group?: ToolGroup;
  description?: string;
  output?: string;
  subtitle?: string;
  experimental?: boolean;
  capability?: "objects" | "paintings" | "sims";
}

export const groups: ToolGroup[] = [
  "Sim appearance",
  "Objects & décor",
  "Image tools",
];
export const destinations: readonly Destination[] = [
  { id: "home", route: "home", label: "Home", panel: "home-tool" },
  {
    id: "package",
    route: "tattoos",
    label: "Tattoos",
    panel: "build-form",
    group: "Sim appearance",
    description: "Build a tattoo package from male and female body textures.",
    output: ".package",
  },
  {
    id: "hair",
    route: "hair",
    label: "Hair recolors",
    panel: "hair-tool",
    group: "Sim appearance",
    description:
      "Create a ZIP of recolor packages using a standard hairstyle or your mesh and recolor packages.",
    output: "ZIP of packages",
  },
  {
    id: "sim",
    route: "sim",
    label: "Sim creator",
    panel: "sim-tool",
    group: "Sim appearance",
    description:
      "Fit a humanoid model to a Sim skeleton. Current test exports support the Adult Male Everyday body only, without the custom head.",
    output: "Experimental .package",
    experimental: true,
    capability: "sims",
  },
  {
    id: "object",
    route: "objects",
    label: "Objects",
    panel: "object-tool",
    group: "Objects & décor",
    description:
      "Turn a 3D model into a decoration package using an existing object’s placement and behavior.",
    output: ".package",
    capability: "objects",
  },
  {
    id: "painting",
    route: "paintings",
    label: "Paintings",
    panel: "painting-tool",
    group: "Objects & décor",
    description:
      "Crop your image into a game frame and download an independent painting package.",
    output: ".package",
    capability: "paintings",
  },
  {
    id: "texture",
    route: "convert",
    label: "Body texture converter",
    panel: "texture-tool",
    group: "Image tools",
    subtitle: "TS4 → TS2",
    description: "Convert a supported TS4 body texture into a TS2 PNG.",
    output: "PNG",
  },
  {
    id: "upscale",
    route: "upscale",
    label: "Image upscaler",
    panel: "upscale-tool",
    group: "Image tools",
    description: "Enlarge an image with a local AI model and download a PNG.",
    output: "PNG",
  },
];

export function resolveDestination(hash: string): {
  destination: Destination;
  unknown: boolean;
} {
  if (!hash || hash === "#" || hash === "#/")
    return { destination: destinations[0], unknown: false };
  const destination = destinations.find((item) => hash === `#/${item.route}`);
  return { destination: destination || destinations[0], unknown: !destination };
}
