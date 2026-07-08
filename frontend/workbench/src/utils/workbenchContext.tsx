import React, { createContext, useContext } from "react";

export type PrimerPairPreset = {
  primer1: string;
  primer2: string;
};

export type Ab1Preset = {
  referenceSegment: string;
  genomicStart?: number;
  genomicEnd?: number;
  leftPrimer: string;
  rightPrimer: string;
};

export type BlastQueryPreset = {
  sequence: string;
  label?: string;
};

export type SequenceInputPreset = {
  sequence: string;
  label?: string;
};

export type GenomeSlicePreset = {
  db: string;
  entry: string;
  start?: number;
  end?: number;
  strand?: "plus" | "minus";
  label?: string;
};

export type WorkbenchContextValue = {
  setActiveTab?: (tabId: string) => void;
  presetReversePair?: PrimerPairPreset | null;
  setPresetReversePair?: (preset: PrimerPairPreset | null) => void;
  presetBlastQuery?: BlastQueryPreset | null;
  setPresetBlastQuery?: (preset: BlastQueryPreset | null) => void;
  presetSequenceInput?: SequenceInputPreset | null;
  setPresetSequenceInput?: (preset: SequenceInputPreset | null) => void;
  presetGenomeSlice?: GenomeSlicePreset | null;
  setPresetGenomeSlice?: (preset: GenomeSlicePreset | null) => void;
  presetAb1?: Ab1Preset | null;
  setPresetAb1?: (preset: Ab1Preset | null) => void;
};

const defaultValue: WorkbenchContextValue = {};

export const WorkbenchContext =
  createContext<WorkbenchContextValue>(defaultValue);

export const useWorkbench = (): WorkbenchContextValue =>
  useContext(WorkbenchContext);
