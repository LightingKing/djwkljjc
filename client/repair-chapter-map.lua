-- Runtime-only client data repair.  The server keeps the full chapter record;
-- this restores the numeric key semantics expected by the stock Lua windows
-- after a raw JSON balance response has been decoded.
local db = CS.DataBindingManager.Instance
local ids = {1001, 1}
local repaired = 0
for _, chapterId in ipairs(ids) do
  local chapter = db:GetData('fci/dungeon/chapter/' .. chapterId)
  local records = chapter and chapter.recordDungeons
  if records then
    local pending = {}
    for key, record in pairs(records) do
      local n = tonumber(key)
      if n and math.floor(n) == n and records[n] == nil then
        pending[n] = record
      end
    end
    for n, record in pairs(pending) do
      records[n] = record
      repaired = repaired + 1
    end
    if next(pending) ~= nil then
      db:SetData('fci/dungeon/chapter/' .. chapterId, chapter)
    end
  end
end
return 'chapter-map repaired=' .. tostring(repaired)
