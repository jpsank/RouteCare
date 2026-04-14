class ApplicationJob < ActiveJob::Base
  # Automatically retry jobs that encountered a deadlock
  # retry_on ActiveRecord::Deadlocked

  # Most jobs are safe to ignore if the underlying records are no longer available
  # discard_on ActiveJob::DeserializationError

  rescue_from(StandardError) do |exception|
    if defined?(Sentry) && Sentry.initialized?
      Sentry.capture_exception(exception, tags: { job: self.class.name }, extra: { arguments: arguments.map(&:inspect) })
    end
    raise exception
  end
end
