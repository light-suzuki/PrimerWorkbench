declare module "seqviz" {
  import * as React from "react";

  export type ViewerType = "linear" | "circular" | "both" | "both_flip";

  export interface SeqVizProps {
    name?: string;
    seq: string;
    viewer?: ViewerType;
    annotations?: any[];
    primers?: any[];
    enzymes?: string[];
    style?: React.CSSProperties;
    [key: string]: any;
  }

  export const SeqViz: React.FC<SeqVizProps>;
}

