import { normalizeLocalDbValue, type LocalBlastDbOption } from "./localBlastDbs";

export const engineDbKey = (option: LocalBlastDbOption): string => normalizeLocalDbValue(option.path ?? option.value);

export const isEngineDbSelected = (selected: string[], option: LocalBlastDbOption): boolean =>
  selected.some(value => normalizeLocalDbValue(value) === engineDbKey(option) || normalizeLocalDbValue(value) === normalizeLocalDbValue(option.value));

export const removeEngineDbSelection = (selected: string[], option: LocalBlastDbOption): string[] =>
  selected.filter(value => !isEngineDbSelected([value], option));

export const resolveEngineDbs = (selected: string[], options: LocalBlastDbOption[], custom: string): string[] => {
  const resolved = selected.map(value => {
    const key = normalizeLocalDbValue(value);
    const option = options.find(o => engineDbKey(o) === key || normalizeLocalDbValue(o.value) === key);
    return option?.path ?? value;
  });
  if (custom.trim()) resolved.push(custom.trim());
  return Array.from(new Set(resolved.filter(Boolean)));
};
