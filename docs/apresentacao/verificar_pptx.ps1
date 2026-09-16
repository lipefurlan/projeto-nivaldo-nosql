# Confere, pelo próprio PowerPoint, se algum texto estoura a caixa.
#
#   powershell -ExecutionPolicy Bypass -File docs\apresentacao\verificar_pptx.ps1
#
# O pptxgenjs posiciona caixas por coordenada e não mede texto. Aqui o
# PowerPoint abre o arquivo, diagrama cada caixa com as fontes de verdade e
# informa a altura e a largura reais do texto (BoundHeight/BoundWidth), que
# são comparadas com o tamanho da caixa. Opcionalmente exporta um PDF só para
# conferência visual — o PDF entregue sai da versão web.

param(
  [string]$Pptx = (Join-Path $PSScriptRoot "..\..\public\static\Torra_e_Terra_NoSQL.pptx"),
  [string]$PdfConferencia = ""
)

$Pptx = (Resolve-Path $Pptx).Path
$tolerancia = 1.5  # pontos

$app = New-Object -ComObject PowerPoint.Application
try {
  # Open(arquivo, ReadOnly, Untitled, WithWindow)
  $apresentacao = $app.Presentations.Open($Pptx, -1, 0, 0)
  $estouros = 0
  $caixas = 0

  foreach ($slide in $apresentacao.Slides) {
    foreach ($forma in $slide.Shapes) {
      if ($forma.HasTextFrame -and $forma.TextFrame.HasText) {
        $caixas++
        $texto = $forma.TextFrame.TextRange
        $sobraAltura = $texto.BoundHeight - $forma.Height
        $sobraLargura = $texto.BoundWidth - $forma.Width
        if ($sobraAltura -gt $tolerancia -or $sobraLargura -gt $tolerancia) {
          $estouros++
          $trecho = $texto.Text.Substring(0, [Math]::Min(60, $texto.Text.Length)) -replace "\s+", " "
          "slide {0,2}: estoura {1,5:N1} pt na altura, {2,5:N1} pt na largura - `"{3}`"" -f $slide.SlideIndex, [Math]::Max(0, $sobraAltura), [Math]::Max(0, $sobraLargura), $trecho
        }
      }
    }
    if (-not $slide.HasNotesPage -or $slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text.Trim() -eq "") {
      "slide {0,2}: SEM nota do apresentador" -f $slide.SlideIndex
      $estouros++
    }
  }

  "{0} slides, {1} caixas de texto conferidas, {2} problema(s)" -f $apresentacao.Slides.Count, $caixas, $estouros

  if ($PdfConferencia) {
    $apresentacao.SaveAs($PdfConferencia, 32)  # 32 = ppSaveAsPDF
    "PDF de conferência: $PdfConferencia"
  }
  $apresentacao.Close()
}
finally {
  $app.Quit()
  [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null
}
