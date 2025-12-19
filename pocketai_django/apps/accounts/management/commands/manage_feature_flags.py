from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import BusinessProfile
from apps.accounts.feature_flags import FeatureFlagService


class Command(BaseCommand):
    help = "Inspect or update per-business knowledge feature flags."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--business-id",
            action="append",
            dest="business_ids",
            default=[],
            help="Target a specific business ID (can be provided multiple times).",
        )
        parser.add_argument(
            "--cohort",
            help="Filter by metadata.cohort value (case-sensitive).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_businesses",
            help="Acknowledge that all businesses should be considered.",
        )
        parser.add_argument(
            "--enable",
            action="append",
            dest="enable",
            default=[],
            help="Feature flag(s) to enable (alias_lookup, entity_chunking, hybrid_search).",
        )
        parser.add_argument(
            "--disable",
            action="append",
            dest="disable",
            default=[],
            help="Feature flag(s) to disable.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview the changes without writing to the database.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_only",
            help="Only list the current values (ignores enable/disable).",
        )

    def handle(self, *args, **options):
        business_ids: list[str] = options.get("business_ids") or []
        cohort: str | None = options.get("cohort")
        include_all: bool = options.get("all_businesses", False)
        enable = [flag.strip().lower() for flag in options.get("enable") or [] if flag]
        disable = [flag.strip().lower() for flag in options.get("disable") or [] if flag]
        dry_run = bool(options.get("dry_run"))
        list_only = bool(options.get("list_only"))

        if not any([business_ids, cohort, include_all]):
            raise CommandError("Specify at least one scope filter via --business-id, --cohort, or --all.")

        queryset = BusinessProfile.objects.all().order_by("created_at")
        if business_ids:
            queryset = queryset.filter(id__in=business_ids)
        if cohort:
            queryset = queryset.filter(metadata__cohort=cohort)
        if not include_all and not business_ids and not cohort:
            raise CommandError("Refusing to update every business without --all acknowledgement.")

        if not queryset.exists():
            raise CommandError("No businesses matched the provided filters.")

        if list_only or (not enable and not disable):
            for business in queryset.iterator():
                state = FeatureFlagService.snapshot(business)
                self.stdout.write(
                    f"{business.id} {business.name}: {state.as_dict()}"
                )
            return

        try:
            results = FeatureFlagService.bulk_apply(
                queryset,
                enable=enable,
                disable=disable,
                dry_run=dry_run,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        changed = 0
        for result in results:
            before = result.before.as_dict()
            after = result.after.as_dict()
            delta = {
                name: after[name]
                for name in FeatureFlagService.ALL_FLAGS
                if before.get(name) != after.get(name)
            }
            if delta:
                changed += 1
            status = "DRY RUN" if dry_run else "UPDATED" if delta else "UNCHANGED"
            self.stdout.write(
                f"{status:<9} {result.business_id} {result.business_name}: before={before} after={after}"
            )
        summary_prefix = "DRY RUN" if dry_run else "APPLIED"
        self.stdout.write(self.style.SUCCESS(f"{summary_prefix}: {changed}/{len(results)} business(es) changed."))
