class ApplicationController < ActionController::Base
  # Only allow modern browsers supporting webp images, web push, badges, import maps, CSS nesting, and CSS :has.
  allow_browser versions: :modern

  before_action :set_sentry_context

  private

  def set_sentry_context
    return unless defined?(Sentry) && Sentry.initialized?

    if respond_to?(:current_user) && current_user
      Sentry.set_user(id: current_user.id, email: current_user.email)
    end
    Sentry.set_tags(controller: controller_name, action: action_name)
  end
end
