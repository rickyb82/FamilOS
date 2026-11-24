package com.familos.homepulse;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.view.View;
import org.json.JSONArray;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/** Native acquisition-time chart. No arrival-time substitution or missing-value filling. */
public final class ChartsView extends View {
    private static final String[] KEYS = {"heart_rate", "spo2", "temperature", "respiratory_rate", "motion"};
    private static final String[] LABELS = {"Battito · bpm", "SpO₂ · %", "Temperatura · °C", "Respiro · resp/min", "Movimento · s/record"};
    private static final int[] COLORS = {0xffc7e99b, 0xff82dcd2, 0xfff0ae96, 0xff9acfe8, 0xffb8abe7};
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final SimpleDateFormat clock = new SimpleDateFormat("HH:mm", Locale.ITALY);
    private final List<List<Point>> series = new ArrayList<>();
    private double start = 0, end = 1, workoutEnd = Double.NaN;
    private final float density;

    private static final class Point {
        final double t, value;
        final boolean warned;
        Point(double t, double value, boolean warned) { this.t = t; this.value = value; this.warned = warned; }
    }

    public ChartsView(Context context) {
        super(context);
        density = getResources().getDisplayMetrics().density;
        clock.setTimeZone(TimeZone.getTimeZone("Europe/Rome"));
        for (String ignored : KEYS) series.add(new ArrayList<>());
        setLayerType(View.LAYER_TYPE_SOFTWARE, null);
        setContentDescription("Grafici storici: battito, ossigenazione, temperatura, respiro e movimento. Nessun campione disponibile.");
    }

    public void setData(JSONObject analysis) {
        double first = Double.POSITIVE_INFINITY, last = Double.NEGATIVE_INFINITY;
        StringBuilder accessibility = new StringBuilder("Orario di acquisizione, fuso di Roma. ");
        for (int index = 0; index < KEYS.length; index++) {
            List<Point> points = series.get(index);
            points.clear();
            JSONArray rows = analysis == null ? null : analysis.optJSONArray(KEYS[index]);
            if (rows != null) for (int i = 0; i < rows.length(); i++) {
                JSONObject row = rows.optJSONObject(i);
                if (row == null) continue;
                double timestamp = row.optDouble("t", Double.NaN), value = row.optDouble("v", Double.NaN);
                if (!Double.isFinite(timestamp) || timestamp <= 0 || !Double.isFinite(value)) continue;
                // This is the main-view policy, not a firmware/biological-validity claim.
                if (index == 0 && (value < 20 || value > 250)) continue;
                JSONArray warnings = row.optJSONArray("warnings");
                points.add(new Point(timestamp, value, warnings != null && warnings.length() > 0));
                first = Math.min(first, timestamp); last = Math.max(last, timestamp);
            }
            points.sort(Comparator.comparingDouble(point -> point.t));
            accessibility.append(LABELS[index]).append(": ").append(points.size()).append(" campioni. ");
        }
        start = analysis == null ? Double.NaN : analysis.optDouble("window_start", Double.NaN);
        end = analysis == null ? Double.NaN : analysis.optDouble("window_end", Double.NaN);
        if (!Double.isFinite(start)) start = Double.isFinite(first) ? first : 0;
        if (!Double.isFinite(end) || end <= start) end = Double.isFinite(last) && last > start ? last : start + 300;
        workoutEnd = analysis == null ? Double.NaN : analysis.optDouble("workout_end", Double.NaN);
        setContentDescription(accessibility.toString());
        invalidate();
    }

    @Override protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        setMeasuredDimension(MeasureSpec.getSize(widthMeasureSpec), resolveSize(dp(484), heightMeasureSpec));
    }

    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float left = dp(9), right = getWidth() - dp(9), top = dp(7), rowHeight = (getHeight() - dp(36)) / 5f;
        if (right <= left) return;
        for (int index = 0; index < KEYS.length; index++) {
            float rowTop = top + index * rowHeight, graphTop = rowTop + dp(27), graphBottom = rowTop + rowHeight - dp(12);
            paint.setStyle(Paint.Style.FILL); paint.setColor(COLORS[index]); paint.setTextSize(dp(11));
            canvas.drawText(LABELS[index], left, rowTop + dp(13), paint);
            paint.setColor(0xff30434a); paint.setStrokeWidth(dp(.65f));
            for (int line = 0; line < 3; line++) {
                float y = graphTop + (graphBottom - graphTop) * line / 2;
                canvas.drawLine(left, y, right, y, paint);
            }
            List<Point> points = series.get(index);
            double low = Double.POSITIVE_INFINITY, high = Double.NEGATIVE_INFINITY;
            int visible = 0;
            for (Point point : points) if (point.t >= start && point.t <= end) {
                low = Math.min(low, point.value); high = Math.max(high, point.value); visible++;
            }
            if (visible == 0) {
                paint.setColor(0xff93a9ad); paint.setTextSize(dp(11));
                canvas.drawText("Nessuna rilevazione nel periodo", left + dp(3), graphTop + dp(21), paint);
                continue;
            }
            double padding = Math.max(index == 0 ? 5 : .5, (high - low) * .15);
            low = index == 4 ? 0 : low - padding;
            high += padding;
            Point previous = null;
            paint.setStrokeWidth(dp(1.7f));
            for (Point point : points) {
                if (point.t < start || point.t > end) continue;
                float x = x(point.t, left, right), y = y(point.value, low, high, graphTop, graphBottom);
                paint.setColor(point.warned ? 0xfff0ae96 : COLORS[index]);
                if (index == 0 && previous != null && point.t - previous.t <= 8 && !previous.warned && !point.warned) {
                    canvas.drawLine(x(previous.t, left, right), y(previous.value, low, high, graphTop, graphBottom), x, y, paint);
                }
                if (index == 4) canvas.drawLine(x, graphBottom, x, y, paint);
                else canvas.drawCircle(x, y, dp(index == 0 ? 2 : 3), paint);
                previous = point;
            }
            if (Double.isFinite(workoutEnd) && workoutEnd >= start && workoutEnd <= end) {
                paint.setColor(0xff7d969b); paint.setStrokeWidth(dp(1));
                float position = x(workoutEnd, left, right);
                for (float dash = graphTop; dash < graphBottom; dash += dp(8)) canvas.drawLine(position, dash, position, Math.min(graphBottom, dash + dp(4)), paint);
            }
        }
        paint.setColor(0xff93a9ad); paint.setTextSize(dp(10));
        for (int i = 0; i < 3; i++) {
            double timestamp = start + (end - start) * i / 2;
            String label = start == 0 ? "—" : clock.format(new Date((long)(timestamp * 1000)));
            float position = x(timestamp, left, right), width = paint.measureText(label);
            canvas.drawText(label, position - (i == 0 ? 0 : i == 2 ? width : width / 2), getHeight() - dp(9), paint);
        }
    }

    private float x(double timestamp, float left, float right) { return left + (float)((timestamp - start) / (end - start)) * (right - left); }
    private float y(double value, double low, double high, float top, float bottom) { return bottom - (float)((value - low) / Math.max(.001, high - low)) * (bottom - top); }
    private int dp(float value) { return Math.round(value * density); }
}
