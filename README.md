# Token haritası (token-lab)

> **Deneme sürümü (0.1).** Şimdilik sadece Windows'ta ve birkaç kişinin verisiyle denendi. Gördüğün sorunu ya da garip sonucu [Issues](https://github.com/merttrkr/claude-token-kit/issues) sayfasına yaz.

Claude Code her istekte modele bir sürü şey gönderir: araç tanımları, skill ve ajan listeleri, CLAUDE.md, hafıza dosyaları, önceki araç çıktıları. Bu araç **senin kendi** konuşma kayıtlarına bakar ve iki soruyu cevaplar:

1. Token'larım nereye gidiyor?
2. Hangileri hiç olmamalı?

Örnek bulgular:

- "41 skill listede, 3'ü kullanıldı; her istekte 4 bin token."
- "Bir MCP sunucusunun 9 aracı 30 günde hiç çağrılmadı."
- "277 kez önbellek soğudu: uzun aradan sonra tüm bağlam yeniden yazıldı."
- "Bir dosya aynı oturumda 22 kez baştan okundu."

Her bulgunun yanında ne kadar tuttuğu, kanıtı ve tek cümlelik çözümü yazar.

## Güvenlik

- **Salt okunur.** Hiçbir ayarı ya da dosyayı değiştirmez. Okuduğu yerler:
  - `~/.claude/projects` altındaki kayıtlar
  - `~/.claude/agents` ve `~/.claude/skills`
  - kayıtlarda adı geçen hafıza index'leri (`MEMORY.md`)
- **Bir şey göndermez.** Rapor ağa çıkmaz. Yalnızca ilk çalıştırmada `uv` paketi kurar; gerekirse Python'u da indirir.
- **Kurulum durumu yok.** Hook, arka plan programı, zamanlanmış görev kurmaz.
- Bir bulguyu uygulamak istersen Claude'a söylersin; o da dosyayı ve farkı gösterip onayını alarak uygular.

## Kurulum

Ön koşul: [uv](https://docs.astral.sh/uv/). Yoksa: `winget install astral-sh.uv`

Claude Code içinden:

```
claude plugin marketplace add https://github.com/merttrkr/claude-token-kit
claude plugin install token-lab@token-kit
```

Sonra Claude'da `/token-lab:token-lab` yaz.

Kurmadan, sadece raporu görmek için:

```
uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why
```

## Rapor nasıl okunur

- **1. bölüm:** her istekte tekrar giden kısım ve payı.
- **2. bölüm:** olmaması gerekenler, haftalık maliyete göre sıralı.
  - Maliyet "girdi-eşdeğeri token"dır. Önbellekten okunan token yaklaşık 0.1, önbelleğe yazılan 1.25–2 sayılır (Claude Code'un fiyat oranları).
  - Abonelikte bunun kotaya etkisi ölçülmedi.
- **"Zaten düzelmiş görünüyor":** son 2 günün oturumlarında artık görünmeyen öğeler. Önerilmez.
- **3. bölüm:** raporun göremedikleri. Oturum başlığı ve compaction istekleri kayıtlara yazılmıyor.
- **4. bölüm:** en çok taşınan dosyalar. İstersen Claude bunları okuyup gereksiz satırları bulur. Bu kısım model yargısıdır.
- **5. bölüm:** ajan işleri. Hangi tekrar eden iş isimli bir ajana taşınabilir, hangi ajan kullanılmıyor.

Rapor ne kadar geriye bakabilir? Claude Code eski kayıtları `cleanupPeriodDays` ayarına göre siler (varsayılan 30 gün). Bu ayar senin.

## Kendi kuralların

Eşikler ve alan adları `src/tokenlab/rules.py` içinde. Değiştirmek için `~/.claude/token-lab.toml` yaz:

```toml
[why]
min_weekly_tokens = 50000

[[areas]]            # işlerini sınıflarken kullanılacak klasörler
name = "specs"
path = "/specs/"

[external]           # aynı sistemi gösteren MCP sunucuları
"mcp-atlassian" = "jira"
```

## Geri bildirim

Sorun ya da öneri için bu reponun **Issues** sayfası. Konuşma metni, dosya yolu ya da şirket bilgisi yapıştırma; issue'lar herkese açık.

## Kaldırma

```
claude plugin uninstall token-lab@token-kit
claude plugin marketplace remove token-kit
```

Rapor hiçbir şey kurmadığı için geride iz kalmaz.

## Geliştirme

```
uv sync
uv run pytest -q
uv run ruff check . && uv run mypy src
```
