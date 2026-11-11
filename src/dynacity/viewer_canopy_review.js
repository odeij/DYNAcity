// Review rings are not trees or measured crown outlines. Off by default.
// Share the page's lexical scope (const DATA / const ui), not window properties.
function canopyReviewLayers() {
  if (!ui.canopyReview || !DATA.canopy_review?.candidates?.length) return [];
  const markers = DATA.canopy_review.candidates.map((c) => ({
    ...c, canopyCandidate: true,
    position: [c.position[0], c.position[1], groundZ(c.position[0], c.position[1]) + 0.35],
  }));
  return [new deck.ScatterplotLayer({
    id: 'canopy-review-candidates', data: markers, pickable: true,
    getPosition: (d) => d.position,
    getRadius: 5, radiusUnits: 'meters', radiusMinPixels: 5, radiusMaxPixels: 14,
    filled: false, stroked: true, getLineWidth: 2, lineWidthUnits: 'pixels',
    getLineColor: (d) => d.review_status === 'held' ? [238, 62, 65] : [255, 179, 0],
    parameters: { depthCompare: 'less-equal', depthWriteEnabled: false },
  })];
}
